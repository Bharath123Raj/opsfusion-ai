"""Actual-video YOLO person inference -> zone observations -> OpsFusion reports."""
import argparse
from datetime import timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from .core import Event, fuse, parse_time
from .vision_logic import ZoneTracker, validate_polygon
from .__main__ import load_events, write_json


def sha256(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def dependencies():
    try:
        import cv2
        import numpy as np
        return cv2,np
    except ImportError as e:
        raise RuntimeError('Install vision dependencies: python -m pip install -r requirements-vision.txt') from e


def config_from(path):
    c=json.loads(path.read_text(encoding='utf-8'))
    validate_polygon(c['polygon'])
    for key in ('asset_id','camera_id'):
        if not isinstance(c.get(key),str) or not c[key].strip(): raise ValueError(f'zone config needs {key}')
    return c


def tracker_from(c):
    return ZoneTracker(c['polygon'],c.get('confirm_samples',2),c.get('max_age_seconds',1),c.get('min_iou',.15))


def annotate(frame, tracks, c, seconds, cv2, np):
    h,w=frame.shape[:2]
    cv2.polylines(frame,[np.array([(round(x*w),round(y*h)) for x,y in c['polygon']],np.int32)],True,(0,165,255),2)
    for track in tracks:
        x1,y1,x2,y2=[round(v*s) for v,s in zip(track['box'],(w,h,w,h))]
        color=(0,0,255) if track['inside'] else (0,200,0)
        cv2.rectangle(frame,(x1,y1),(x2,y2),color,2)
        cv2.circle(frame,(round(track['foot'][0]*w),round(track['foot'][1]*h)),4,color,-1)
        cv2.putText(frame,f"ID {track['track_id']} {track['state']} {track['confidence']:.2f}",(x1,max(18,y1-8)),cv2.FONT_HERSHEY_SIMPLEX,.5,color,1)
    cv2.putText(frame,f"{seconds:.2f}s | {c['camera_id']}",(10,25),cv2.FONT_HERSHEY_SIMPLEX,.65,(255,255,255),2)
    return frame


def pick(args):
    cv2,np=dependencies()
    if args.output.exists(): raise ValueError('Zone file already exists; choose a new --output filename')
    cap=cv2.VideoCapture(str(args.video))
    try:
        fps=cap.get(cv2.CAP_PROP_FPS)
        if args.at<0 or not math.isfinite(args.at): raise ValueError('--at must be nonnegative')
        if fps<=0: raise ValueError('Could not read video FPS')
        cap.set(cv2.CAP_PROP_POS_FRAMES,round(args.at*fps))
        ok,frame=cap.read()
        if not ok: raise ValueError('Cannot decode selected frame')
    finally: cap.release()
    h,w=frame.shape[:2]; scale=min(1,1280/w,800/h)
    shown=cv2.resize(frame,(round(w*scale),round(h*scale)))
    sh,sw=shown.shape[:2]; points=[]; name='OpsFusion: click vertices | Enter save | R reset | Esc cancel'
    def click(event,x,y,flags,param):
        if event==cv2.EVENT_LBUTTONDOWN: points.append([min(1,max(0,x/sw)),min(1,max(0,y/sh))])
    cv2.namedWindow(name);cv2.setMouseCallback(name,click)
    try:
        while True:
            view=shown.copy()
            pixels=np.array([(round(x*sw),round(y*sh)) for x,y in points],np.int32)
            if len(points)>=2: cv2.polylines(view,[pixels],len(points)>=3,(0,165,255),2)
            for x,y in pixels: cv2.circle(view,(int(x),int(y)),4,(0,0,255),-1)
            cv2.imshow(name,view); key=cv2.waitKey(30)&255
            if key==27: return
            if cv2.getWindowProperty(name,cv2.WND_PROP_VISIBLE)<1: return
            if key in (ord('r'),ord('R')): points.clear()
            if key in (10,13):
                try: validate_polygon(points)
                except ValueError as e: print(e);continue
                if not args.asset_id.strip() or not args.camera_id.strip(): raise ValueError('asset and camera IDs required')
                write_json(args.output,{'asset_id':args.asset_id,'camera_id':args.camera_id,'polygon':points,
                    'confirm_samples':2,'max_age_seconds':1.0,'min_iou':.15})
                print(f'Saved zone to {args.output}');return
    finally: cv2.destroyAllWindows()


class YoloDetector:
    def __init__(self,model,device,confidence):
        try:
            import ultralytics
            from ultralytics import YOLO
        except ImportError as e: raise RuntimeError('Install requirements-vision.txt before running video inference') from e
        if str(model)=='yolov8n.pt' and os.environ.get('OPSFUSION_YOLO_WEIGHTS'):
            model=os.environ['OPSFUSION_YOLO_WEIGHTS']
            if not Path(model).is_file():raise ValueError('Configured detector weights are unavailable')
        self.model=YOLO(model);self.device=device;self.confidence=confidence
        person_ids=[int(k) for k,v in self.model.names.items() if str(v).lower()=='person']
        if len(person_ids)!=1: raise ValueError('Detector weights must include exactly one class named person')
        self.person_id=person_ids[0]
        weights=Path(getattr(self.model,'ckpt_path',model))
        self.metadata={'name':model,'ultralytics_version':ultralytics.__version__,
            'weights_sha256':sha256(weights) if weights.is_file() else None,
            'device':device,'confidence_threshold':confidence,'person_class_id':self.person_id}
    def __call__(self,frame):
        result=self.model.predict(frame,classes=[self.person_id],conf=self.confidence,device=self.device,verbose=False)[0]
        h,w=frame.shape[:2];detections=[]
        for coords,confidence in zip(result.boxes.xyxy.cpu().tolist(),result.boxes.conf.cpu().tolist()):
            b=[min(1,max(0,v/s)) for v,s in zip(coords,(w,h,w,h))]
            if b[0]<b[2] and b[1]<b[3]: detections.append({'box':b,'confidence':float(confidence)})
        return detections


def run(args,detector=None):
    """Detector injection is used only for labelled software integration tests."""
    cv2,np=dependencies()
    start=parse_time(args.start_time);c=config_from(args.zone);tracker=tracker_from(c)
    if not args.video.is_file(): raise ValueError(f'Video not found: {args.video}')
    if args.output.exists(): raise ValueError('Output directory already exists; use a new --output to preserve evidence')
    if not math.isfinite(args.sample_fps) or args.sample_fps<=0: raise ValueError('--sample-fps must be positive')
    if not math.isfinite(args.conf) or not .6<=args.conf<=1: raise ValueError('--conf must be between 0.6 and 1 (fusion evidence threshold)')
    if args.max_seconds is not None and (not math.isfinite(args.max_seconds) or args.max_seconds<=0): raise ValueError('--max-seconds must be positive')
    extra=load_events(args.merge_events) if args.merge_events else []
    # Never discard extra events by overwriting their source file with outputs.
    cap=cv2.VideoCapture(str(args.video));writer=None
    try:
        fps=float(cap.get(cv2.CAP_PROP_FPS));total=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if not cap.isOpened() or not math.isfinite(fps) or fps<=0: raise ValueError('Cannot open video or invalid FPS')
        stride=max(1,round(fps/args.sample_fps));effective_fps=fps/stride
        if 1/effective_fps>tracker.max_age: raise ValueError('Sampling interval exceeds max_age_seconds; increase sample FPS or max_age')
        model=detector or YoloDetector(args.model,args.device,args.conf)
        metadata=getattr(model,'metadata',{'name':'injected_test_detector','test_only':True})
        video_hash=sha256(args.video)
        token=hashlib.sha256(json.dumps({'video':video_hash,'zone':c,'start':start.isoformat(),'model':metadata,'fps':effective_fps},sort_keys=True).encode()).hexdigest()[:16]
        args.output.mkdir(parents=True);(args.output/'evidence').mkdir()
        events=[];latencies=[];sampled=0;read_frames=0;last_progress=time.perf_counter();wall_start=last_progress;max_people=0
        with (args.output/'detections.jsonl').open('w',encoding='utf-8') as raw:
            while True:
                index=read_frames;seconds=index/fps
                if args.max_seconds is not None and seconds>=args.max_seconds: break
                ok,frame=cap.read()
                if not ok: break
                read_frames+=1
                if index%stride: continue
                tick=time.perf_counter();detections=model(frame);latencies.append((time.perf_counter()-tick)*1000)
                detections=[d for d in detections if d['confidence']>=args.conf]
                tracks=tracker.update(seconds,detections);sampled+=1;max_people=max(max_people,len(tracks))
                raw.write(json.dumps({'frame_index':index,'offset_seconds':seconds,'tracks':tracks})+'\n')
                annotated=annotate(frame.copy(),tracks,c,seconds,cv2,np)
                if writer is None:
                    h,w=frame.shape[:2];writer=cv2.VideoWriter(str(args.output/'annotated.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),effective_fps,(w,h))
                    if not writer.isOpened(): raise RuntimeError('Could not create annotated MP4; check OpenCV codec support')
                writer.write(annotated)
                for t in tracks:
                    if t['transition'] not in ('entry','initial_presence'): continue
                    kind='zone_entry' if t['transition']=='entry' else 'zone_presence'
                    event_id=f"video-{token}-{index}-{t['track_id']}-{kind}"
                    snapshot=args.output/'evidence'/f'{event_id}.jpg'
                    if not cv2.imwrite(str(snapshot),annotated): raise RuntimeError('Could not save evidence snapshot')
                    evidence=snapshot.resolve().as_uri()+f'#video_offset_seconds={seconds:.3f}&frame={index}'
                    description=(f"Person track {t['track_id']} moved from confirmed outside to confirmed inside" if kind=='zone_entry' else f"Person track {t['track_id']} first observed inside; entry time unknown")
                    events.append(Event(event_id,(start+timedelta(seconds=seconds)).isoformat(),c['asset_id'],'video',kind,c['camera_id'],evidence,
                        f'{description} at video offset {seconds:.3f}s; bottom-center anchor; heuristic track identity.',t['confidence'],bool(args.synthetic_video or metadata.get('test_only',False))))
                if time.perf_counter()-last_progress>15:
                    print(f'Processed {seconds:.1f}s / {sampled} sampled frames / {len(events)} observations',flush=True);last_progress=time.perf_counter()
        if sampled==0: raise ValueError('No sampled frames decoded')
        if args.max_seconds is None and total>0 and read_frames<total-1:
            raise RuntimeError(f'Video decoding stopped early ({read_frames}/{total} frames); outputs are incomplete')
        write_json(args.output/'zone.json',c)
        (args.output/'video_events.jsonl').write_text(''.join(json.dumps(e.to_dict())+'\n' for e in events),encoding='utf-8')
        (args.output/'combined_events.jsonl').write_text(''.join(json.dumps(e.to_dict())+'\n' for e in events+extra),encoding='utf-8')
        result=fuse(events+extra);write_json(args.output/'incidents.json',result)
        elapsed=time.perf_counter()-wall_start
        sorted_latency=sorted(latencies)
        write_json(args.output/'run_manifest.json',{'status':'completed','source_video':args.video.resolve().as_uri(),
            'source_sha256':video_hash,'start_time':start.isoformat(),'timestamp_method':'constant-FPS frame_index / source_fps; user-provided start time',
            'source_fps':fps,'effective_sample_fps':effective_fps,'stride':stride,'decoded_frames':read_frames,'sampled_frames':sampled,
            'processed_duration_seconds':read_frames/fps,'zone':c,'detector':metadata,'synthetic_video':bool(args.synthetic_video),
            'video_observations':len(events),'entry_observations':sum(e.kind=='zone_entry' for e in events),
            'initial_presence_observations':sum(e.kind=='zone_presence' for e in events),'merged_events':len(extra),
            'incidents':len(result['incidents']),'max_simultaneous_detections':max_people,'wall_seconds':elapsed,
            'mean_detector_ms':sum(latencies)/len(latencies),'p95_detector_ms':sorted_latency[max(0,math.ceil(.95*len(latencies))-1)],
            'limitations':['Offline replay, not live CCTV monitoring.','Greedy IoU tracks may split or swap under occlusion.',
                'Variable-frame-rate video must be converted to constant FPS before use.','Person confidence does not measure entry accuracy.',
                'No authorization verification; zone observations are candidates for review.','Annotated preview has no audio.']})
        print(f"COMPLETE: {sampled} sampled frames, {len(events)} video observations, {len(result['incidents'])} incidents. Outputs: {args.output}")
        return result
    except Exception as error:
        if args.output.is_dir():
            write_json(args.output/'run_manifest.json',{'status':'failed','error':str(error),'outputs_are_incomplete':True})
        raise
    finally:
        cap.release()
        if writer is not None: writer.release()


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    picker=sub.add_parser('pick-zone',help='Click polygon vertices on a video frame')
    picker.add_argument('--video',type=Path,required=True);picker.add_argument('--output',type=Path,default=Path('config/zone.json'))
    picker.add_argument('--asset-id',default='warehouse-zone-01');picker.add_argument('--camera-id',default='camera-01');picker.add_argument('--at',type=float,default=0)
    video=sub.add_parser('run',help='Infer people, detect entries and fuse evidence')
    video.add_argument('--video',type=Path,required=True);video.add_argument('--zone',type=Path,required=True)
    video.add_argument('--start-time',required=True,help='Recording start ISO time with timezone; not current processing time')
    video.add_argument('--output',type=Path,default=Path('runs/video-01'));video.add_argument('--model',default='yolov8n.pt')
    video.add_argument('--device',default='cpu');video.add_argument('--conf',type=float,default=.6)
    video.add_argument('--sample-fps',type=float,default=5);video.add_argument('--max-seconds',type=float)
    video.add_argument('--merge-events',type=Path,help='Optional real, time-aligned events JSONL from other adapters')
    video.add_argument('--synthetic-video',action='store_true',help='Mark composed/generated video evidence synthetic')
    args=parser.parse_args()
    try:
        if args.command=='pick-zone': pick(args)
        else: run(args)
    except (ValueError,RuntimeError,OSError,KeyError) as e:
        print(f'ERROR: {e}',file=sys.stderr);raise SystemExit(1)

if __name__=='__main__': main()
