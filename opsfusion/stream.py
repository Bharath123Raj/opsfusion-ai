"""Live HTTP stream setup/client. Demo measurements are generated and explicitly synthetic."""
import argparse
from datetime import datetime,timezone
import getpass
import json
from pathlib import Path
import random
import time
from urllib.parse import urlsplit
import uuid

from .accounts import Accounts
from .runtime import data_root
from .stream_store import StreamStore


class StreamClientError(ValueError):
    """A deliberately sanitized user-facing client error."""
    pass


def local_url(value):
    parts=urlsplit(value)
    if parts.scheme!='http' or parts.hostname not in ('127.0.0.1','localhost','::1') or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('','/'):
        raise ValueError('Use a loopback HTTP API origin, e.g. http://127.0.0.1:8000')
    parts.port
    return value.rstrip('/')


def demo_reading(index,asset_id,message_id,timestamp,rng):
    abnormal=5<=index<14 or 22<=index<30
    return {'message_id':message_id,'timestamp':timestamp,'asset_id':asset_id,'synthetic':True,
            'vibration_rms_mm_s':round(max(0,rng.gauss(2.,.05))+(2.5 if abnormal else 0),5),
            'temperature_c':round(rng.gauss(55.,.2)+(8 if abnormal else 0),5)}


def send_demo(url,username,password,baseline_id=None,seconds=35,client=None):
    import httpx
    url=local_url(url)
    if type(seconds)!=int or not 1<=seconds<=120:raise ValueError('Test stream duration must be 1–120 seconds')
    owned=client is None
    client=client or httpx.Client(base_url=url,timeout=10,trust_env=False,follow_redirects=False)
    token=None;sid=None
    def request(method,path,payload=None):
        for attempt in range(3):
            try:
                response=client.request(method,path,json=payload,headers={} if token is None else {'Authorization':'Bearer '+token})
                if response.status_code>=400:
                    if response.status_code==401:raise StreamClientError('Sign-in failed or session expired. Use your OpsFusion password; start the API in the existing installation that holds your account. Do not create another account.')
                    if response.status_code==403:raise StreamClientError('This account cannot ingest data. Use an enabled admin or operator account.')
                    if response.status_code==409:raise StreamClientError('Stream conflict. Close an earlier open stream in the dashboard; use chronological unique inputs.')
                    raise StreamClientError('API request rejected (HTTP '+str(response.status_code)+'). Check account role, baseline and clocks; see local API documentation.')
                return response.json()
            except (httpx.TimeoutException,httpx.NetworkError):
                if attempt==2:raise StreamClientError('API connection failed. Start the local API from your existing installation folder, then retry.') from None
                time.sleep(.5)
    try:
        token=request('POST','/auth/login',{'username':username,'password':password})['access_token']
        baselines=request('GET','/v1/streams/baselines')
        if baseline_id is None:
            chosen=next((b for b in baselines if 'warehouse-zone-01' in b['metadata'] and b['metadata']['warehouse-zone-01']['trained_synthetic']),None)
            if chosen is None:raise StreamClientError('Run python -m opsfusion.stream prepare-demo --username YOUR_ADMIN first')
            baseline_id=chosen['baseline_id']
        else:
            chosen=next((b for b in baselines if b['baseline_id']==baseline_id),None)
            if chosen is None or 'warehouse-zone-01' not in chosen['metadata'] or not chosen['metadata']['warehouse-zone-01']['trained_synthetic']:
                raise StreamClientError('Test sender requires a registered synthetic warehouse-zone-01 baseline')
        # Stream creation is not retried after a lost acknowledgement: avoid orphan duplicate sessions.
        response=client.post('/v1/streams',json={'baseline_id':baseline_id,'asset_id':'warehouse-zone-01','source_id':'synthetic-http-test','synthetic':True},headers={'Authorization':'Bearer '+token})
        if response.status_code!=200:raise StreamClientError('Cannot open test stream; close any earlier open stream for warehouse-zone-01 in the dashboard')
        sid=response.json()['stream_id'];print('SYNTHETIC HTTP STREAM: '+sid,flush=True)
        rng=random.Random(42);start=time.monotonic();accepted=0;emitted=0
        for index in range(seconds):
            while time.monotonic()-start<index:time.sleep(max(0,min(.1,index-(time.monotonic()-start))))
            timestamp=datetime.now(timezone.utc).isoformat()
            body=demo_reading(index,'warehouse-zone-01','reading-'+str(index),timestamp,rng)
            result=request('POST',f'/v1/streams/{sid}/readings',body);accepted+=1;emitted+=int(result['event_id'] is not None)
            print(f'Reading {index+1}/{seconds}: candidate={result["score"]["candidate"]}, confirmed={result["score"]["alert"]}, new observation={result["event_id"] is not None}',flush=True)
            if index in (9,25,28):
                log={'message_id':'controller-'+str(index),'timestamp':timestamp,'asset_id':'warehouse-zone-01','synthetic':True,
                     'level':'WARN' if index!=28 else 'ERROR','code':'MOTOR_OVERLOAD' if index!=28 else 'PROTECTIVE_SHUTDOWN',
                     'message':'Authored controller test message; not a real machine report.'}
                result=request('POST',f'/v1/streams/{sid}/logs',log);emitted+=int(result['event_id'] is not None)
        request('POST',f'/v1/streams/{sid}/close')
        print(f'COMPLETE: {accepted} generated readings, {emitted} adapter observations. View saved HTTP stream and incidents in localhost. No hardware accuracy claim.',flush=True)
        return sid
    finally:
        if token:
            if sid:
                try:request('POST',f'/v1/streams/{sid}/close')
                except Exception:print('Stream could not be closed. Close it in the dashboard before starting another.',flush=True)
            try:request('POST','/auth/logout')
            except Exception:pass
        if owned:client.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    for name in ('prepare-demo','register-baseline'):
        q=sub.add_parser(name);q.add_argument('--username',required=True)
        if name=='register-baseline':q.add_argument('--train',type=Path,required=True);q.add_argument('--validation',type=Path,required=True)
    q=sub.add_parser('demo');q.add_argument('--username',required=True);q.add_argument('--url',default='http://127.0.0.1:8000');q.add_argument('--baseline-id');q.add_argument('--seconds',type=int,default=35)
    args=p.parse_args();root=data_root();token=None
    try:
        if args.command=='demo':
            send_demo(args.url,args.username,getpass.getpass('OpsFusion password: '),args.baseline_id,args.seconds);return
        store=StreamStore(root);token=store.accounts.login(args.username,getpass.getpass('Admin password: '))
        demo_root=root if (root/'data/sensor-demo/train.csv').is_file() else Path(__file__).resolve().parents[1]
        train=demo_root/'data/sensor-demo/train.csv' if args.command=='prepare-demo' else args.train
        validation=demo_root/'data/sensor-demo/validation.csv' if args.command=='prepare-demo' else args.validation
        registered=store.register(token,train,validation)
        print('REGISTERED: '+registered['baseline_id'])
        print('Assets: '+', '.join(registered['assets']))
        print('Baseline training includes synthetic data: '+str(any(m['trained_synthetic'] for m in registered['assets'].values())))
    except KeyboardInterrupt:print('Test sender stopped')
    except StreamClientError as exc:p.error(str(exc))
    except (ValueError,PermissionError,RuntimeError,OSError):p.error('Stream setup/client failed. Check account, local API, baseline files or close an earlier open stream. No credentials were printed.')
    finally:
        if token:store.accounts.logout(token)


if __name__=='__main__':main()
