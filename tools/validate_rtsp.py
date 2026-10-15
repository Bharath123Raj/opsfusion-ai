"""Optional loopback transport check. Requires FFmpeg, vision dependencies and YOLO weights.

Serves a local recording through RTSP + interleaved RTP/JPEG, then runs the real
worker. This is a composed transport fixture, never a hardware-camera benchmark.
RFC 2326 section 10.12 defines the interleaved packet framing.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from opsfusion.history import History
from opsfusion import __version__
from opsfusion.video import sha256


class RTSPFixture:
    def __init__(self, video, disconnect_after=None):
        self.video=Path(video);self.disconnect_after=disconnect_after
        self.stopped=threading.Event();self.connections=0;self.failures=[];self.methods=[]
        self.server=socket.socket();self.server.bind(('127.0.0.1',0));self.server.listen()
        self.server.settimeout(.2);self.port=self.server.getsockname()[1]
        self.thread=threading.Thread(target=self._serve,daemon=True);self.thread.start()

    def _serve(self):
        while not self.stopped.is_set():
            try:
                client,_=self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.connections+=1
            try:
                self._client(client,self.connections==1)
            except (OSError,ValueError):
                pass

    def _client(self, client, first_connection):
        receiver=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);receiver.bind(('127.0.0.1',0));receiver.settimeout(.2)
        port=receiver.getsockname()[1];client.settimeout(.2)
        lock=threading.Lock();finished=threading.Event();sender=None;relay=None;channel=0
        def forward():
            began=time.monotonic()
            while not finished.is_set() and not self.stopped.is_set():
                if first_connection and self.disconnect_after and time.monotonic()-began>self.disconnect_after:
                    client.shutdown(socket.SHUT_RDWR);finished.set();break
                try:
                    packet,_=receiver.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    with lock:client.sendall(b'$'+bytes([channel])+struct.pack('!H',len(packet))+packet)
                except OSError:
                    finished.set();break
        pending=b''
        try:
            while not finished.is_set() and not self.stopped.is_set():
                try:
                    data=client.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    break
                pending+=data
                # Consume client interleaved RTCP packets before parsing requests.
                while pending.startswith(b'$') and len(pending)>=4:
                    size=struct.unpack('!H',pending[2:4])[0]
                    if len(pending)<size+4:
                        break
                    pending=pending[size+4:]
                if pending.startswith(b'$') or b'\r\n\r\n' not in pending:
                    continue
                request,pending=pending.split(b'\r\n\r\n',1)
                text=request.decode('utf-8');method=text.split(' ',1)[0]
                self.methods.append(method)
                sequence=re.search(r'CSeq:\s*(\d+)',text,re.I).group(1)
                headers=['CSeq: '+sequence,'Session: loopback-fixture'];body=''
                if method=='OPTIONS':
                    headers.append('Public: OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN, GET_PARAMETER')
                elif method=='DESCRIBE':
                    body='v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=OpsFusion composed RTSP fixture\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\na=control:*\r\nm=video 0 RTP/AVP 26\r\na=rtpmap:26 JPEG/90000\r\na=control:track1\r\n'
                    headers+=['Content-Type: application/sdp',f'Content-Base: rtsp://127.0.0.1:{self.port}/fixture/']
                elif method=='SETUP':
                    transport=re.search(r'Transport:\s*([^\r\n]+)',text,re.I).group(1)
                    match=re.search(r'interleaved=(\d+)-(\d+)',transport)
                    if not match:
                        raise ValueError('Fixture supports TCP interleaving only')
                    channel=int(match.group(1))
                    headers.append('Transport: RTP/AVP/TCP;unicast;interleaved='+match.group(1)+'-'+match.group(2))
                elif method=='PLAY':
                    headers.append('Range: npt=0.000-')
                headers.append('Content-Length: '+str(len(body.encode())))
                with lock:
                    client.sendall(('RTSP/1.0 200 OK\r\n'+'\r\n'.join(headers)+'\r\n\r\n'+body).encode())
                if method=='PLAY' and sender is None:
                    sender=subprocess.Popen(['ffmpeg','-hide_banner','-loglevel','error','-re','-stream_loop','-1','-i',str(self.video),
                                             '-an','-vf','scale=640:480','-pix_fmt','yuvj420p','-c:v','mjpeg','-threads','1','-huffman','default','-q:v','4',
                                             '-f','rtp','-payload_type','26',f'rtp://127.0.0.1:{port}'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                    relay=threading.Thread(target=forward,daemon=True);relay.start()
                if method=='TEARDOWN':
                    break
        finally:
            finished.set()
            if sender is not None:
                sender.terminate()
                try:sender.wait(timeout=2)
                except subprocess.TimeoutExpired:sender.kill();sender.wait(timeout=2)
                errors=sender.stderr.read().decode(errors='replace')
                if errors:self.failures.append(errors[:1000])
            receiver.close();client.close()
            if relay is not None:relay.join(timeout=1)

    def close(self):
        self.stopped.set();self.server.close();self.thread.join(timeout=3)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video',type=Path,required=True)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not shutil.which('ffmpeg'):
        parser.error('Optional transport validation requires FFmpeg on PATH')
    if args.output.exists():
        parser.error('Choose a new output directory')
    args.output.mkdir(parents=True)
    zone=args.output/'zone.json'
    zone.write_text(json.dumps({'asset_id':'transport-fixture','camera_id':'rtsp-fixture',
                               'polygon':[[.45,.35],[.9,.35],[.9,.95],[.45,.95]],
                               'confirm_samples':2,'max_age_seconds':1,'min_iou':.15}))
    server=RTSPFixture(args.video,disconnect_after=3)
    try:
        env=dict(os.environ);env['OPSFUSION_RTSP_TEST']=f'rtsp://fixture-user:fixture-secret@127.0.0.1:{server.port}/fixture'
        command=[sys.executable,'-m','opsfusion.live','run','--rtsp-env','OPSFUSION_RTSP_TEST',
                 '--zone',str(zone.resolve()),'--database',str((args.output/'history.sqlite3').resolve()),
                 '--output',str((args.output/'live').resolve()),'--model',str(args.model.resolve()),
                 '--max-seconds','18','--synthetic']
        run=subprocess.run(command,cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=55)
        history=History(args.output/'history.sqlite3');session=history.sessions()[0];reports=history.incidents()
        export=json.dumps(history.export())
        passed=(run.returncode==0 and session['sampled_frames']>0 and session['event_count']>0
                and session['connection_epoch']>=1 and server.connections>=2
                and all(r['current_report']['synthetic'] for r in reports)
                and 'fixture-secret' not in export and 'fixture-secret' not in run.stdout+run.stderr)
        result={'application_version':__version__,'passed':passed,'transport':'actual loopback RTSP/TCP + RTP/JPEG',
                'detector':'actual CPU YOLOv8n; no injected detections','composed_source':True,
                'connections':server.connections,'forced_disconnect':True,
                'connection_epoch':session['connection_epoch'],'sampled_frames':session['sampled_frames'],
                'observations':session['event_count'],'saved_reports':len(reports),'state':session['state'],
                'credential_absent_from_history_and_logs':'fixture-secret' not in export+run.stdout+run.stderr,
                'source_sha256':sha256(args.video),'weights_sha256':sha256(args.model),
                'capture_settings':json.loads(session['config']),
                'scope':'Transport/reconnect/persistence integration. Hardware webcam and deployment CCTV untested; no accuracy score.'}
        (args.output/'validation.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2))
        if not passed:
            print('Worker output:',run.stdout,run.stderr,'Protocol methods:',server.methods,'Sender errors:',server.failures)
            raise SystemExit(1)
    finally:
        server.close()


if __name__=='__main__':
    main()
