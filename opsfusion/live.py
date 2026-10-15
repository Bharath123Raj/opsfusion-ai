"""One local camera worker. Webcam/RTSP capture is isolated from inference and UI."""
import argparse
from datetime import timedelta
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import queue
import subprocess
import sys
import time
from urllib.parse import urlsplit

from .core import Event, parse_time
from .history import History, utc_now
from .video import YoloDetector, annotate, config_from, dependencies, tracker_from


def validate_source(mode, source):
    if mode=='webcam':
        if type(source)!=int or not 0<=source<=20:
            raise ValueError('Webcam index must be an integer from 0 to 20')
    elif mode=='rtsp':
        if not isinstance(source,str) or len(source)>2048 or any(c in source for c in '\r\n\x00'):
            raise ValueError('Invalid RTSP source')
        try:
            parts=urlsplit(source)
            if parts.scheme not in ('rtsp','rtsps') or not parts.hostname:
                raise ValueError()
            parts.port
        except ValueError:
            raise ValueError('Use an rtsp:// or rtsps:// camera URL') from None
    elif mode=='file_replay':
        if not Path(source).is_file():
            raise ValueError('Replay recording does not exist')
    else:
        raise ValueError('Choose webcam, RTSP, or file replay')


def settings(zone, sample_fps=5, confidence=.6, max_seconds=3600, synthetic=False):
    tracker=tracker_from(zone)
    if isinstance(sample_fps,bool) or not math.isfinite(sample_fps) or not 1<=sample_fps<=10 or 1/sample_fps>tracker.max_age:
        raise ValueError('Sample FPS must be 1–10 and compatible with tracker max age')
    if not math.isfinite(confidence) or not .6<=confidence<=1:
        raise ValueError('Confidence must be 0.6–1')
    if not math.isfinite(max_seconds) or not 1<=max_seconds<=86400:
        raise ValueError('Session duration must be 1–86400 seconds')
    return {**{k:v for k,v in zone.items() if k not in ('asset_id','camera_id')},
            'sample_fps':sample_fps,'confidence':confidence,'max_seconds':max_seconds,
            'synthetic':bool(synthetic),'quiet_gap_seconds':20,'window_seconds':90,
            'max_events':5000,'max_episode_events':200}


def atomic_bytes(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_bytes(data)
    os.replace(temporary,path)


def _offer_latest(outbox, message):
    try:
        outbox.put_nowait(message)
        return
    except queue.Full:
        pass
    try:
        outbox.get_nowait()
    except queue.Empty:
        pass
    try:
        outbox.put_nowait(message)
    except queue.Full:
        pass


def _capture(mode, source, outbox, replay_start):
    """Runs in a spawned child. Suppress native backend errors that may expose URLs."""
    with open(os.devnull,'w') as sink:
        os.dup2(sink.fileno(),2)
    cap=None
    terminal=('error','capture_failed')
    try:
        cv2,_=dependencies()
        if mode=='rtsp':
            cap=cv2.VideoCapture(source,cv2.CAP_FFMPEG,
                                 [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,5000,cv2.CAP_PROP_READ_TIMEOUT_MSEC,5000])
        else:
            cap=cv2.VideoCapture(source if mode=='webcam' else str(source))
        if not cap.isOpened():
            terminal=('error','camera_open_failed');return
        fps=cap.get(cv2.CAP_PROP_FPS) if mode=='file_replay' else None
        if mode=='file_replay' and (not math.isfinite(fps) or fps<=0):
            terminal=('error','invalid_replay_fps');return
        base=parse_time(replay_start) if mode=='file_replay' else None
        began=time.monotonic();index=0
        while True:
            if mode=='file_replay':
                remaining=began+index/fps-time.monotonic()
                if remaining>0:
                    time.sleep(min(remaining,1/fps))
            ok,frame=cap.read()
            if not ok:
                terminal=('eof',) if mode=='file_replay' else ('error','camera_read_failed')
                break
            received=time.monotonic()
            stamp=(base+timedelta(seconds=index/fps)).isoformat() if base else utc_now()
            if frame.shape[1]>1280:
                frame=cv2.resize(frame,(1280,round(frame.shape[0]*1280/frame.shape[1])))
            _offer_latest(outbox,('frame',frame,stamp,received,index))
            index+=1
    except Exception:
        terminal=('error','capture_failed')
    finally:
        if cap is not None:
            cap.release()
        # Keep EOF/error behind pending frames. Parent kills a blocked child on stop.
        try:
            outbox.put(terminal,timeout=1)
        except queue.Full:
            _offer_latest(outbox,terminal)


class Processor:
    """Testable bridge from decoded frames to durable observations."""
    def __init__(self, history, sid, root, zone, detector):
        self.history=history;self.sid=sid;self.root=Path(root)/sid
        self.zone=zone;self.detector=detector;self.tracker=tracker_from(zone)
        self.cfg=history.session(sid)['config'];self.frames=0;self.previous_stamp=None
        self.first_moment=None;self.epoch=history.session(sid)['connection_epoch']

    def reset(self):
        self.history.boundary(self.sid);self.epoch+=1;self.tracker=tracker_from(self.zone)
        self.first_moment=None

    def process(self, frame, stamp, moment, index):
        cv2,np=dependencies()
        if self.history.should_stop(self.sid):
            return []
        dt=parse_time(stamp)
        if self.previous_stamp is not None and dt<self.previous_stamp:
            raise ValueError('Camera receipt clock moved backwards')
        self.previous_stamp=dt
        if self.first_moment is None:
            self.first_moment=moment
        seconds=moment-self.first_moment
        detections=[d for d in self.detector(frame) if d['confidence']>=self.cfg['confidence']]
        if self.history.should_stop(self.sid):
            return []
        tracks=self.tracker.update(seconds,detections)
        annotated=annotate(frame.copy(),tracks,self.zone,seconds,cv2,np)
        cv2.putText(annotated,dt.strftime('%Y-%m-%d %H:%M:%S UTC'),(10,50),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1)
        ok,jpeg=cv2.imencode('.jpg',annotated)
        if not ok:
            raise RuntimeError('Snapshot encoding failed')
        events=[]
        for t in tracks:
            if t['transition'] not in ('entry','initial_presence'):
                continue
            kind='zone_entry' if t['transition']=='entry' else 'zone_presence'
            eid=f'live-{self.sid}-{self.epoch}-{index}-{t["track_id"]}-{kind}'
            path=self.root/'evidence'/f'{eid}.jpg'
            atomic_bytes(path,jpeg.tobytes())
            description=(f'Person track {t["track_id"]} moved from confirmed outside to confirmed inside'
                         if kind=='zone_entry' else f'Person track {t["track_id"]} first observed inside; entry time unknown')
            events.append(Event(eid,dt.isoformat(),self.zone['asset_id'],'video',kind,self.zone['camera_id'],
                                path.resolve().as_uri(),description+'; bottom-center anchor; heuristic identity within this connection.',
                                t['confidence'],bool(self.cfg['synthetic'] or getattr(self.detector,'metadata',{}).get('test_only',False))))
        # Evidence must exist before the DB commits its references.
        self.history.ingest(self.sid,events)
        atomic_bytes(self.root/'latest.jpg',jpeg.tobytes())
        self.frames+=1
        self.history.heartbeat(self.sid,frames=self.frames)
        return events


def _wait(history, sid, duration):
    end=time.monotonic()+duration
    while time.monotonic()<end:
        if history.should_stop(sid):
            return False
        time.sleep(min(.1,max(0,end-time.monotonic())))
    return True


def worker(history, sid, root, zone, mode, source, replay_start=None, model='yolov8n.pt'):
    """Latest-frame policy: drop buffered frames rather than accumulate capture latency."""
    validate_source(mode,source)
    if mode=='file_replay':
        parse_time(replay_start)
    cfg=history.session(sid)['config'];capture=None;outbox=None
    state='stopped';error_code=None;began=time.monotonic();attempts=0
    if history.should_stop(sid):
        history.finish(sid,'stopped')
        return history.session(sid)
    if not history.claim_worker(sid,os.getpid()):
        raise ValueError('Worker session already claimed')
    try:
        detector=YoloDetector(model,'cpu',cfg['confidence'])
        import torch
        torch.set_num_threads(min(4,os.cpu_count() or 1))
        # Only retain safe model provenance; never a source URL or local credential.
        metadata={k:v for k,v in detector.metadata.items() if k in ('ultralytics_version','weights_sha256','device','confidence_threshold','person_class_id')}
        with history.connect() as db:
            cfg['detector']=metadata
            db.execute('UPDATE sessions SET config=? WHERE session_id=?',(json.dumps(cfg),sid))
        processor=Processor(history,sid,root,zone,detector)
        while not history.should_stop(sid):
            if time.monotonic()-began>=cfg['max_seconds']:
                state='completed';break
            context=mp.get_context('spawn');outbox=context.Queue(maxsize=2)
            capture=context.Process(target=_capture,args=(mode,source,outbox,replay_start),daemon=True)
            capture.start();last_frame=time.monotonic();last_sample=None;connected=False
            failure='capture_unresponsive'
            while not history.should_stop(sid):
                if time.monotonic()-began>=cfg['max_seconds']:
                    state='completed';break
                history.heartbeat(sid,'running' if connected else 'starting',frames=processor.frames)
                try:
                    message=outbox.get(timeout=.2)
                except queue.Empty:
                    if not capture.is_alive() or time.monotonic()-last_frame>12:
                        break
                    continue
                if message[0]=='eof':
                    state='completed';failure=None;break
                if message[0]=='error':
                    failure=message[1];break
                _,frame,stamp,moment,index=message
                last_frame=time.monotonic();connected=True;attempts=0
                # Avoid stale buffered observations. Inference may reduce effective FPS.
                if time.monotonic()-moment>2:
                    continue
                if last_sample is not None and moment-last_sample<.95/cfg['sample_fps']:
                    continue
                last_sample=moment
                processor.process(frame,stamp,moment,index)
            capture.terminate();capture.join(timeout=2)
            if capture.is_alive():
                capture.kill();capture.join(timeout=2)
            outbox.close();outbox.cancel_join_thread();capture=None;outbox=None
            if state=='completed' or history.should_stop(sid):
                break
            attempts+=1
            if mode=='file_replay' or attempts>5:
                state='failed';error_code=failure or 'capture_retry_exhausted';break
            processor.reset()
            history.heartbeat(sid,'reconnecting',frames=processor.frames,error_code=failure)
            if not _wait(history,sid,min(8,2**(attempts-1))):
                break
    except KeyboardInterrupt:
        state='stopped'
    except Exception as exc:
        if history.should_stop(sid):
            state='stopped'
        else:
            state='failed'
            message=str(exc)
            error_code=('capacity_reached' if 'capacity reached' in message else
                        'clock_moved_backwards' if 'clock moved backwards' in message else 'detector_or_processing_failed')
    finally:
        if capture is not None:
            if capture.is_alive():
                capture.terminate();capture.join(timeout=2)
            if capture.is_alive():
                capture.kill();capture.join(timeout=2)
        if outbox is not None:
            outbox.close();outbox.cancel_join_thread()
        if state=='completed' and history.session(sid)['sampled_frames']==0:
            state='failed';error_code='no_frames_received'
        history.finish(sid,state,error_code)
    return history.session(sid)


def launch(root, zone, mode, source, sample_fps=5, max_seconds=3600, synthetic=False):
    """Dashboard launches a worker, not a browser-session thread. No shell interpolation."""
    validate_source(mode,source)
    if mode not in ('webcam','rtsp'):
        raise ValueError('Dashboard live launch supports webcam/RTSP')
    root=Path(root).resolve();history=History(root/'runs'/'history.sqlite3')
    cfg=settings(zone,sample_fps=sample_fps,max_seconds=max_seconds,synthetic=synthetic)
    sid=history.start(zone['camera_id'],zone['asset_id'],mode,cfg)
    zone_path=root/'runs'/'live'/sid/'zone.json'
    atomic_bytes(zone_path,json.dumps(zone).encode())
    args=[sys.executable,'-m','opsfusion.live','run','--zone',str(zone_path),
          '--database',str(history.path),'--output',str(root/'runs'/'live'),
          '--session-id',sid,'--sample-fps',str(sample_fps),'--max-seconds',str(max_seconds)]
    env=dict(os.environ)
    if mode=='rtsp':
        env['OPSFUSION_RTSP_URL']=source
        args+=['--rtsp-env','OPSFUSION_RTSP_URL']
    else:
        args+=['--webcam',str(source)]
    if synthetic:
        args+=['--synthetic']
    try:
        subprocess.Popen(args,cwd=root,env=env,stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0)
    except OSError:
        history.finish(sid,'failed','worker_launch_failed')
        raise RuntimeError('Worker could not start') from None
    return sid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    run=sub.add_parser('run')
    source=run.add_mutually_exclusive_group(required=True)
    source.add_argument('--webcam',type=int)
    source.add_argument('--rtsp-env',help='Name of environment variable holding the RTSP URL; credentials are never saved')
    source.add_argument('--replay-video',type=Path,help='Paced recording through the live worker; not a live-camera accuracy test')
    run.add_argument('--start-time',help='Required actual recording time for --replay-video')
    run.add_argument('--zone',type=Path,required=True)
    run.add_argument('--database',type=Path,default=Path('runs/history.sqlite3'))
    run.add_argument('--output',type=Path,default=Path('runs/live'))
    run.add_argument('--session-id',help=argparse.SUPPRESS)
    run.add_argument('--sample-fps',type=float,default=5)
    run.add_argument('--max-seconds',type=float,default=3600)
    run.add_argument('--synthetic',action='store_true')
    run.add_argument('--model',default='yolov8n.pt')
    stop=sub.add_parser('stop');stop.add_argument('--session-id',required=True)
    stop.add_argument('--database',type=Path,default=Path('runs/history.sqlite3'))
    args=parser.parse_args()
    try:
        h=History(args.database)
        if args.command=='stop':
            h.request_stop(args.session_id);print('Stop requested');return
        zone=config_from(args.zone)
        mode='webcam' if args.webcam is not None else 'rtsp' if args.rtsp_env else 'file_replay'
        value=args.webcam if mode=='webcam' else os.environ.get(args.rtsp_env,'') if mode=='rtsp' else args.replay_video
        validate_source(mode,value)
        if mode=='file_replay':
            if not args.start_time:
                raise ValueError('--replay-video requires --start-time with timezone')
            parse_time(args.start_time)
        cfg=settings(zone,args.sample_fps,max_seconds=args.max_seconds,synthetic=args.synthetic)
        sid=args.session_id
        if sid is None:
            sid=h.start(zone['camera_id'],zone['asset_id'],mode,cfg)
        else:
            existing=h.session(sid)
            if existing['state']!='starting' or existing['camera_id']!=zone['camera_id'] or existing['asset_id']!=zone['asset_id'] or existing['mode']!=mode or existing['config']!=cfg:
                raise ValueError('Worker session/configuration mismatch')
        print('SESSION:',sid,flush=True)
        result=worker(h,sid,args.output,zone,mode,value,args.start_time,args.model)
        print('STATE:',result['state'],'FRAMES:',result['sampled_frames'],'OBSERVATIONS:',result['event_count'],'ERROR:',result['error_code'])
        if result['state']=='failed':
            raise SystemExit(1)
    except (ValueError,OSError,RuntimeError):
        print('ERROR: Live worker could not start. Check the zone, source, dependencies and active worker list.',file=sys.stderr)
        raise SystemExit(1)


if __name__=='__main__':
    main()
