"""Run a marked camera + HTTP integration fixture without physical hardware."""
import argparse
from datetime import datetime, timezone
import getpass
from pathlib import Path
import time
import uuid

from .correlation import WatchStore
from .history import History
from .runtime import data_root
from .live import Processor, settings
from .stream import local_url, StreamClientError


class FixtureColorDetector:
    """Detect the green test rectangle. Never used by real camera workers."""
    metadata = {'test_only':True, 'detector':'synthetic_green_rectangle_fixture'}

    def __call__(self, frame):
        import cv2
        mask = cv2.inRange(frame, (0,150,0), (100,255,100))
        points = cv2.findNonZero(mask)
        if points is None: return []
        x,y,w,h = cv2.boundingRect(points);height,width = frame.shape[:2]
        return [{'box':[x/width,y/height,(x+w)/width,(y+h)/height], 'confidence':1.}]


def make_clip(path):
    import cv2
    import numpy as np
    path.parent.mkdir(parents=True,exist_ok=True)
    writer = cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),2.,(480,320))
    if not writer.isOpened(): raise RuntimeError('Could not create the synthetic fixture video')
    try:
        for index in range(36):
            frame = np.zeros((320,480,3),dtype=np.uint8)
            x = .56 if 8 <= index < 20 or index >= 26 else .44
            cv2.rectangle(frame,(int((x-.14)*480),64),(int((x+.14)*480),262),(0,220,0),-1)
            cv2.putText(frame,'SYNTHETIC FIXTURE - not person detection',(8,310),cv2.FONT_HERSHEY_SIMPLEX,.52,(255,255,255),1)
            writer.write(frame)
    finally: writer.release()


def run_demo(root, url, username, password, paced=True):
    import cv2
    import httpx
    root = Path(root).resolve();url = local_url(url)
    store = WatchStore(root);history = store.history
    token = None;sid = None;camera = None;wid = None;cap = None
    with httpx.Client(base_url=url,timeout=10,trust_env=False,follow_redirects=False) as client:
        def request(method,path,body=None):
            try:
                response = client.request(method,path,json=body,headers={} if not token else {'Authorization':'Bearer '+token})
            except (httpx.NetworkError,httpx.TimeoutException):
                raise StreamClientError('Local API connection failed. Start the API from your upgraded installation folder.') from None
            if response.status_code == 401: raise StreamClientError('Sign-in failed or expired. Use your existing OpsFusion account and password; the API must run from that installation.')
            if response.status_code == 403: raise StreamClientError('Use an enabled admin or operator account for this integration test.')
            if response.status_code == 404: raise StreamClientError('API is missing Step13 routes. Upgrade the installation and restart the API.')
            if response.status_code >= 400: raise StreamClientError('API rejected the test (HTTP '+str(response.status_code)+'). Close any earlier warehouse-zone-01 HTTP stream; confirm the Step13 API is running.')
            return response.json()
        try:
            token = request('POST','/auth/login',{'username':username,'password':password})['access_token']
            remote = request('GET','/v1/watches/installation')['installation_id']
            try: local = store.installation_id(token)
            except PermissionError: raise StreamClientError('This terminal and the API use different installation folders. Run both from the upgraded Step10 folder holding your account.') from None
            if local != remote: raise StreamClientError('This terminal and the API use different installation folders. Run both from the upgraded Step10 folder holding your account.')
            store.accounts.require(token,'analyse')
            baseline = next((b for b in request('GET','/v1/streams/baselines') if b['metadata'].get('warehouse-zone-01',{}).get('trained_synthetic')),None)
            if baseline is None: raise StreamClientError('Prepare the marked baseline first: python -m opsfusion.stream prepare-demo --username YOUR_ADMIN')
            sid = request('POST','/v1/streams',{'baseline_id':baseline['baseline_id'],'asset_id':'warehouse-zone-01','source_id':'linked-fixture','synthetic':True})['stream_id']
            zone = {'asset_id':'warehouse-zone-01','camera_id':'fixture-camera-'+uuid.uuid4().hex[:8],
                    'polygon':[[.5,.1],[.95,.1],[.95,.95],[.5,.95]],'confirm_samples':2,'max_age_seconds':1.,'min_iou':.15}
            camera = history.start(zone['camera_id'],zone['asset_id'],'file_replay',settings(zone,sample_fps=2,max_seconds=30,synthetic=True))
            clip = root/'runs/live'/camera/'synthetic-fixture.avi';make_clip(clip)
            cap = cv2.VideoCapture(str(clip))
            if not cap.isOpened(): raise RuntimeError('Could not decode the synthetic fixture')
            processor = Processor(history,camera,root/'runs/live',zone,FixtureColorDetector())
            wid = request('POST','/v1/watches',{'camera_session':camera,'http_stream':sid,'horizon_seconds':10.})['watch_id']
            print('SYNTHETIC LINKED WATCH: '+wid,flush=True)
            print('Generated clip + green-rectangle fixture detector; no hardware or YOLO accuracy test.',flush=True)
            started = time.monotonic();video_events = 0
            for index in range(36):
                if paced:
                    while time.monotonic()-started < index/2:
                        time.sleep(max(0,min(.1,index/2-(time.monotonic()-started))))
                ok,frame = cap.read()
                if not ok: raise RuntimeError('Synthetic fixture ended unexpectedly')
                stamp = datetime.now(timezone.utc).isoformat()
                video_events += len(processor.process(frame,stamp,index/2,index))
                if index % 2 == 0:
                    second = index//2;abnormal = 5 <= second < 12
                    reading = {'message_id':'reading-'+str(second),'timestamp':stamp,'asset_id':zone['asset_id'],'synthetic':True,
                               'vibration_rms_mm_s':4.5 if abnormal else 2.,'temperature_c':63. if abnormal else 55.}
                    request('POST',f'/v1/streams/{sid}/readings',reading)
                    if second in (9,12):
                        request('POST',f'/v1/streams/{sid}/logs',{'message_id':'controller-'+str(second),'timestamp':stamp,
                                'asset_id':zone['asset_id'],'synthetic':True,'level':'WARN' if second==9 else 'ERROR',
                                'code':'MOTOR_OVERLOAD' if second==9 else 'PROTECTIVE_SHUTDOWN',
                                'message':'Authored integration fixture; not a real machine record.'})
            data = request('GET',f'/v1/watches/{wid}/export')
            count = len(data['incidents'])
            if video_events != 2 or count != 2: raise RuntimeError('Integration fixture did not produce its expected two entries and two joint reports')
            if not all(r['current_report']['synthetic'] for r in data['incidents']): raise RuntimeError('Fixture synthetic provenance was lost')
            print(f'COMPLETE: 36 decoded frames, 18 readings, 2 controller records, {video_events} zone entries, {count} joint reports. All synthetic. View Linked asset watches.',flush=True)
            return wid
        finally:
            if cap is not None: cap.release()
            if camera: history.finish(camera,'completed')
            if token:
                if sid:
                    try: request('POST',f'/v1/streams/{sid}/close')
                    except Exception: print('Close the test HTTP stream in the dashboard before retrying.',flush=True)
                try: request('POST','/auth/logout')
                except Exception: pass


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command',required=True)
    q = sub.add_parser('demo');q.add_argument('--username',required=True);q.add_argument('--url',default='http://127.0.0.1:8000')
    args = p.parse_args()
    try: run_demo(data_root(),args.url,args.username,getpass.getpass('OpsFusion password: '))
    except KeyboardInterrupt: print('Integration fixture stopped; saved evidence remains.')
    except StreamClientError as exc: p.error(str(exc))
    except (ValueError,PermissionError,RuntimeError,OSError): p.error('Integration fixture could not complete. Verify the same upgraded installation folder, baseline and local API. No credentials were printed.')


if __name__ == '__main__': main()
