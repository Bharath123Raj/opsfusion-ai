"""Supervise the local API/dashboard pair. The deployment gateway is separate."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request

from .access import settings
from .runtime import data_root


@contextmanager
def exclusive(root):
    """OS lock releases on process exit; a stale pathname cannot block recovery."""
    path=Path(root)/'runs/.service.lock';path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as f:
        f.seek(0);f.write(b'0');f.flush();f.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise ValueError('This data directory is in use by the supervised service or maintenance') from None
        try:yield
        finally:
            f.seek(0)
            if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(f,fcntl.LOCK_UN)


def healthy(api_port,dashboard_port):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        for port,path in [(api_port,'/health'),(dashboard_port,'/_stcore/health')]:
            with opener.open(f'http://127.0.0.1:{port}{path}',timeout=2) as r:
                if r.status!=200:return False
        return True
    except Exception:return False


def commands(api_port,dashboard_port,bind):
    from urllib.parse import urlsplit
    cfg=settings();public=urlsplit(cfg.origin) if cfg else None
    if bind=='0.0.0.0' and cfg is None:raise ValueError('A non-loopback backend requires access mode and a private container network')
    code=Path(__file__).resolve().parents[1]
    return [[sys.executable,'-m','uvicorn','opsfusion.api:app','--host',bind,'--port',str(api_port),'--no-access-log','--no-server-header','--no-proxy-headers'],
            [sys.executable,'-m','streamlit','run',str(code/'dashboard.py'),'--server.address',bind,'--server.port',str(dashboard_port),
             '--server.headless','true','--browser.gatherUsageStats','false','--server.enableCORS','true','--server.enableXsrfProtection','true',
             '--browser.serverAddress',public.hostname if public else '127.0.0.1','--browser.serverPort',str(public.port or (443 if public.scheme=='https' else 80)) if public else str(dashboard_port)]]


def _stop(children):
    for p in children:
        if p.poll() is None:p.terminate()
    deadline=time.monotonic()+12
    for p in children:
        try:p.wait(timeout=max(.1,deadline-time.monotonic()))
        except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)


def run(root,api_port=8000,dashboard_port=8504,bind='127.0.0.1',max_restarts=3):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    if api_port==dashboard_port or any(type(p)!=int or not 1024<=p<=65535 for p in (api_port,dashboard_port)):raise ValueError('Use two different ports from 1024 to 65535')
    cmds=commands(api_port,dashboard_port,bind)
    for port in (api_port,dashboard_port):
        with socket.socket() as s:
            s.settimeout(.3)
            if s.connect_ex(('127.0.0.1',port))==0:
                raise ValueError('A service port is already in use; stop the earlier dashboard/API first')
    stopping=False
    def stop(*args):
        nonlocal stopping
        stopping=True
    previous={sig:signal.signal(sig,stop) for sig in (signal.SIGTERM,signal.SIGINT)}
    state=root/'runs/service-state.json';children=[]
    env=dict(os.environ);env['OPSFUSION_DATA_ROOT']=str(root)
    env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])+os.pathsep+env.get('PYTHONPATH','')
    def record(value):
        tmp=state.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2));tmp.replace(state)
    try:
        with exclusive(root):
            for cycle in range(max_restarts+1):
                if stopping:break
                started=time.monotonic();children=[]
                try:
                    for cmd in cmds:children.append(subprocess.Popen(cmd,cwd=root,env=env))
                    record({'state':'starting','supervisor_pid':os.getpid(),'api_pid':children[0].pid,'dashboard_pid':children[1].pid,'restart_count':cycle})
                    failures=0;ready=False
                    while not stopping:
                        if any(p.poll() is not None for p in children):break
                        ok=healthy(api_port,dashboard_port)
                        if ok:
                            failures=0
                            if not ready:
                                ready=True;record({'state':'running','supervisor_pid':os.getpid(),'api_pid':children[0].pid,'dashboard_pid':children[1].pid,'restart_count':cycle})
                                print('OPSFUSION READY: API and dashboard healthy. Data directory: '+str(root),flush=True)
                        elif ready:
                            failures+=1
                            if failures>=3:break
                        elif time.monotonic()-started>60:break
                        for _ in range(20):
                            if stopping:break
                            time.sleep(.1)
                finally:_stop(children)
                if stopping:break
                if cycle==max_restarts:
                    record({'state':'failed','restart_count':cycle});return 1
                record({'state':'restarting','restart_count':cycle+1})
                for _ in range(10*(2**cycle)):
                    if stopping:break
                    time.sleep(.1)
            record({'state':'stopped'});return 0
    finally:
        _stop(children)
        for sig,handler in previous.items():signal.signal(sig,handler)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-root',type=Path);p.add_argument('--api-port',type=int,default=8000);p.add_argument('--dashboard-port',type=int,default=8504);p.add_argument('--bind',choices=['127.0.0.1','0.0.0.0'],default='127.0.0.1');p.add_argument('--healthcheck',action='store_true')
    a=p.parse_args()
    if a.healthcheck:raise SystemExit(0 if healthy(a.api_port,a.dashboard_port) else 1)
    try:raise SystemExit(run(a.data_root or data_root(),a.api_port,a.dashboard_port,a.bind))
    except (ValueError,OSError) as exc:p.error(str(exc))


if __name__=='__main__':main()
