"""Chronological per-asset sensor baseline and causal persistence adapter."""
import argparse
import csv
from datetime import timedelta
import hashlib
import json
import math
from pathlib import Path
import sys
import time
from .core import Event, fuse, parse_time
from .__main__ import load_events, write_json

FEATURES=('vibration_rms_mm_s','temperature_c')
FIELDS=('timestamp','asset_id',*FEATURES,'synthetic')


def libs():
    try:
        import numpy as np
        import sklearn
        import joblib
        from sklearn.ensemble import IsolationForest
        return np,sklearn,joblib,IsolationForest
    except ImportError as e: raise RuntimeError('Install requirements-sensor.txt') from e


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path):
    rows=[];seen=set()
    with path.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames is None or set(reader.fieldnames)!=set(FIELDS) or len(reader.fieldnames)!=len(FIELDS):
            raise ValueError(f'CSV must contain exactly {FIELDS}; labels belong in a separate file')
        for line,r in enumerate(reader,2):
            try:
                dt=parse_time(r['timestamp']);asset=r['asset_id'].strip()
                if not asset: raise ValueError('asset_id is empty')
                values=[float(r[k]) for k in FEATURES]
                if not all(math.isfinite(v) for v in values): raise ValueError('missing or nonfinite measurements')
                if values[0]<0: raise ValueError('RMS vibration cannot be negative')
                if r['synthetic'].lower() not in ('true','false'): raise ValueError('synthetic must be true or false')
                if (asset,dt) in seen: raise ValueError('duplicate asset timestamp')
                seen.add((asset,dt))
                rows.append({'timestamp':dt.isoformat(),'asset_id':asset,'values':values,'synthetic':r['synthetic'].lower()=='true','csv_line':line})
            except (ValueError,TypeError,AttributeError) as e: raise ValueError(f'{path}:{line}: {e}') from e
    if not rows: raise ValueError('CSV has no measurements')
    # Input order may vary; physical CSV lines are retained for evidence references.
    return sorted(rows,key=lambda r:(r['asset_id'],parse_time(r['timestamp'])))


def group(rows):
    result={}
    for row in rows: result.setdefault(row['asset_id'],[]).append(row)
    return result


def fit_baselines(train,val,quantile=.995,seed=42):
    np,sklearn,joblib,IF=libs()
    if not .9<=quantile<=1: raise ValueError('quantile must be between .9 and 1')
    train_g,val_g=group(train),group(val)
    if train_g.keys()!=val_g.keys(): raise ValueError('train and validation assets must match')
    models={};metadata={}
    for asset,t in train_g.items():
        v=val_g[asset]
        if len(t)<120 or len(v)<80: raise ValueError('Each asset needs at least 120 training and 80 validation rows')
        if parse_time(t[-1]['timestamp'])>=parse_time(v[0]['timestamp']): raise ValueError('Training must end strictly before validation for each asset')
        x=np.array([r['values'] for r in t]);y=np.array([r['values'] for r in v])
        center=np.median(x,axis=0);scale=1.4826*np.median(np.abs(x-center),axis=0)
        # Fixed scale floor in declared units; constant sensors do not divide by zero.
        scale=np.maximum(scale,np.array([.05,.25]))
        model=IF(n_estimators=200,max_samples=min(256,len(t)),contamination='auto',random_state=seed,n_jobs=1)
        model.fit((x-center)/scale)
        scores=-model.score_samples((y-center)/scale)
        threshold=float(np.quantile(scores,quantile))
        cadence=float(np.median([(parse_time(b['timestamp'])-parse_time(a['timestamp'])).total_seconds() for a,b in zip(t,t[1:])]))
        models[asset]={'estimator':model,'center':center,'scale':scale,'threshold':threshold,'cadence_seconds':cadence,
            'validation_end':v[-1]['timestamp'],'trained_synthetic':any(r['synthetic'] for r in t+v)}
        metadata[asset]={'train_rows':len(t),'validation_rows':len(v),'train_start':t[0]['timestamp'],'train_end':t[-1]['timestamp'],
            'validation_start':v[0]['timestamp'],'validation_end':v[-1]['timestamp'],'center':center.tolist(),'robust_scale':scale.tolist(),
            'if_threshold':threshold,'cadence_seconds':cadence,'trained_synthetic':models[asset]['trained_synthetic']}
    return {'format':'opsfusion_sensor_v1','features':list(FEATURES),'sklearn_version':sklearn.__version__,'seed':seed,
        'quantile':quantile,'models':models},metadata


def confirm_flags(rows,candidates,persistence=3,max_gap_seconds=2):
    """Only current and preceding rows determine an alert; no retroactive flags."""
    if type(persistence)!=int or persistence<1: raise ValueError('persistence must be positive integer')
    if not math.isfinite(max_gap_seconds) or max_gap_seconds<=0: raise ValueError('max_gap_seconds must be positive')
    if len(rows)!=len(candidates): raise ValueError('candidate length mismatch')
    flags=[];episodes=[];streak=0;start=None;active=None;previous=None
    for idx,(r,candidate) in enumerate(zip(rows,candidates)):
        dt=parse_time(r['timestamp'])
        gap=previous is not None and (dt-previous).total_seconds()>max_gap_seconds
        if previous is not None and dt<=previous: raise ValueError('timestamps must increase per asset')
        if gap or not candidate:
            if active is not None: episodes.append(active)
            active=None;streak=0;start=None
        if candidate:
            if streak==0:start=idx
            streak+=1
            if streak>=persistence:
                if active is None:active={'start_index':start,'confirmation_index':idx,'end_index':idx}
                else:active['end_index']=idx
        flags.append(bool(candidate and streak>=persistence));previous=dt
    if active is not None:episodes.append(active)
    return flags,episodes


def score_rows(bundle,rows,persistence=3,hard_z=6,soft_z=3,max_gap_factor=2):
    np,sklearn,joblib,IF=libs()
    if bundle.get('format')!='opsfusion_sensor_v1' or bundle.get('features')!=list(FEATURES): raise ValueError('Incompatible model bundle')
    if bundle['sklearn_version']!=sklearn.__version__: raise ValueError('Model sklearn version differs; retrain in the current environment')
    if not 0<soft_z<=hard_z or not math.isfinite(hard_z) or not math.isfinite(soft_z): raise ValueError('Require finite 0 < soft_z <= hard_z')
    if not math.isfinite(max_gap_factor) or max_gap_factor<=0:raise ValueError('max_gap_factor must be positive')
    scored=[];episodes=[]
    for asset,measurements in group(rows).items():
        if asset not in bundle['models']: raise ValueError(f'Unknown asset {asset}; train a baseline first')
        m=bundle['models'][asset]
        if parse_time(measurements[0]['timestamp'])<=parse_time(m['validation_end']): raise ValueError('Inference must begin strictly after validation end')
        x=np.array([r['values'] for r in measurements]);z=(x-m['center'])/m['scale'];scores=-m['estimator'].score_samples(z)
        maxz=np.max(np.abs(z),axis=1)
        candidates=(maxz>=hard_z)|((scores>m['threshold'])&(maxz>=soft_z))
        flags,segments=confirm_flags(measurements,candidates,persistence,m['cadence_seconds']*max_gap_factor)
        base=len(scored)
        for i,r in enumerate(measurements):
            scored.append({**r,'if_score':float(scores[i]),'if_threshold':m['threshold'],'robust_z':z[i].tolist(),
                'candidate':bool(candidates[i]),'alert':flags[i],'synthetic':bool(r['synthetic'] or m['trained_synthetic'])})
        for e in segments:episodes.append({'asset_id':asset,**{k:v+base for k,v in e.items()}})
    return scored,episodes


def episode_events(scored,episodes,csv_path,output,token):
    events=[];(output/'evidence').mkdir()
    for number,episode in enumerate(episodes,1):
        first=scored[episode['start_index']];confirmed=scored[episode['confirmation_index']];last=scored[episode['end_index']]
        event_id=f'sensor-{token}-{number:04d}';path=output/'evidence'/f'{event_id}.json'
        segment=scored[episode['start_index']:episode['end_index']+1]
        write_json(path,{'asset_id':episode['asset_id'],'source_csv':csv_path.resolve().as_uri(),'onset':first['timestamp'],
            'confirmed_at':confirmed['timestamp'],'end':last['timestamp'],'measurements':segment,
            'note':'CSV line references and detector scores; cause is unconfirmed. Evidence includes later episode rows; alert timestamp remains causal confirmation.'})
        directions=', '.join(f'{name}: {confirmed["values"][i]:.3f}, robust z={confirmed["robust_z"][i]:.2f}' for i,name in enumerate(FEATURES))
        events.append(Event(event_id,confirmed['timestamp'],episode['asset_id'],'sensor','sensor_anomaly',f'{episode["asset_id"]}-sensors',path.resolve().as_uri(),
            f'Persistent sensor deviation confirmed; {directions}; IF score {confirmed["if_score"]:.4f}, threshold {confirmed["if_threshold"]:.4f}. Cause unconfirmed.',
            1.0,any(r['synthetic'] for r in segment)))
    return events


def write_scores(path,scored):
    columns=[*FIELDS,'csv_line','if_score','if_threshold','vibration_robust_z','temperature_robust_z','candidate','alert']
    with path.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader()
        for r in scored:
            writer.writerow({'timestamp':r['timestamp'],'asset_id':r['asset_id'],FEATURES[0]:r['values'][0],FEATURES[1]:r['values'][1],
                'synthetic':str(r['synthetic']).lower(),'csv_line':r['csv_line'],'if_score':r['if_score'],'if_threshold':r['if_threshold'],
                'vibration_robust_z':r['robust_z'][0],'temperature_robust_z':r['robust_z'][1],
                'candidate':str(r['candidate']).lower(),'alert':str(r['alert']).lower()})


def evaluate(scored,labels_path):
    labels=json.loads(labels_path.read_text(encoding='utf-8'))
    if labels.get('schema')!='opsfusion_sensor_labels_v1':raise ValueError('Unknown label schema')
    lookup={}
    for row in labels['point_labels']:
        key=(row['asset_id'],parse_time(row['timestamp']).isoformat())
        if key in lookup or type(row['anomaly']) is not bool:raise ValueError('Invalid or duplicate ground-truth label')
        lookup[key]=row['anomaly']
    keys={(r['asset_id'],r['timestamp']) for r in scored}
    if keys!=lookup.keys():raise ValueError('Ground-truth points must exactly match inference rows')
    tp=fp=tn=fn=0
    for r in scored:
        truth=lookup[(r['asset_id'],r['timestamp'])];pred=r['alert']
        if truth and pred:tp+=1
        elif pred:fp+=1
        elif truth:fn+=1
        else:tn+=1
    safe=lambda a,b:a/b if b else None
    precision=safe(tp,tp+fp);recall=safe(tp,tp+fn)
    normal_hours=0
    for asset,rs in group(scored).items():
        if len(rs)>1:
            deltas=[(parse_time(b['timestamp'])-parse_time(a['timestamp'])).total_seconds() for a,b in zip(rs,rs[1:])]
            cadence=sorted(deltas)[len(deltas)//2]
            normal_hours+=sum(not lookup[(asset,r['timestamp'])] for r in rs)*cadence/3600
    event_metrics=None
    if 'fault_intervals' in labels:
        predicted=[]
        for asset,rs in group(scored).items():
            if len(rs)<2: continue
            gaps=[(parse_time(b['timestamp'])-parse_time(a['timestamp'])).total_seconds() for a,b in zip(rs,rs[1:])]
            cadence=sorted(gaps)[len(gaps)//2]
            active=None
            for row in rs:
                dt=parse_time(row['timestamp'])
                if active is not None and (not row['alert'] or dt-active['end']>timedelta(seconds=2*cadence)):
                    predicted.append(active);active=None
                if row['alert']:
                    if active is None:active={'asset_id':asset,'start':dt,'end':dt,'cadence':cadence}
                    else:active['end']=dt
            if active is not None:predicted.append(active)
        truth=[]
        for interval in labels['fault_intervals']:
            start,end=parse_time(interval['start']),parse_time(interval['end_exclusive'])
            if end<=start:raise ValueError('Ground-truth interval must have positive duration')
            truth.append({**interval,'start_dt':start,'end_dt':end})
        pairs=[]
        for pi,pred in enumerate(predicted):
            for ti,fault in enumerate(truth):
                if pred['asset_id']!=fault['asset_id']:continue
                overlap=(min(pred['end']+timedelta(seconds=pred['cadence']),fault['end_dt'])-max(pred['start'],fault['start_dt'])).total_seconds()
                if overlap>0:pairs.append((overlap,pi,ti))
        used_p=set();used_t=set();matches=[]
        for overlap,pi,ti in sorted(pairs,reverse=True):
            if pi not in used_p and ti not in used_t:
                used_p.add(pi);used_t.add(ti)
                matches.append({'asset_id':truth[ti]['asset_id'],'fault_type':truth[ti].get('fault_type'),
                    'delay_seconds':(predicted[pi]['start']-truth[ti]['start_dt']).total_seconds()})
        event_metrics={'matching':'one-to-one greedy maximum temporal overlap on same asset; any positive overlap qualifies',
            'expected_fault_intervals':len(truth),'predicted_alert_episodes':len(predicted),'matched':len(matches),
            'precision':safe(len(matches),len(predicted)),'recall':safe(len(matches),len(truth)),
            'false_alert_episodes_per_normal_asset_hour':safe(len(predicted)-len(matches),normal_hours),
            'matches':matches,'missed_faults':[{'asset_id':f['asset_id'],'fault_type':f.get('fault_type')} for n,f in enumerate(truth) if n not in used_t],
            'note':'Permissive temporal event matching; synthetic fault recall is not causal diagnosis accuracy.'}
    return {'scope':labels.get('scope','provided labels'),'synthetic':labels.get('synthetic'),
        'tp':tp,'fp':fp,'tn':tn,'fn':fn,'precision':precision,'recall':recall,
        'f1':2*precision*recall/(precision+recall) if precision is not None and recall is not None and precision+recall else 0,
        'normal_asset_hours_estimated':normal_hours,'false_alert_samples_per_normal_asset_hour':safe(fp,normal_hours),
        'event_metrics':event_metrics,
        'note':'Point metrics on causal persisted flags, not incident accuracy. Normal exposure assumes median cadence; no live latency or real-world accuracy claim.'}


def train(args):
    np,sklearn,joblib,IF=libs()
    if args.output.exists():raise ValueError('Model output folder exists; choose a new --output')
    t,v=read_csv(args.train),read_csv(args.validation)
    bundle,meta=fit_baselines(t,v,args.quantile,args.seed)
    args.output.mkdir(parents=True)
    joblib.dump(bundle,args.output/'sensor.joblib')
    write_json(args.output/'training_manifest.json',{'schema':'opsfusion_sensor_training_v1','sklearn_version':sklearn.__version__,
        'features':list(FEATURES),'train_sha256':digest(args.train),'validation_sha256':digest(args.validation),
        'seed':args.seed,'calibration_quantile':args.quantile,'assets':meta,
        'rule':'abs robust z >= hard_z OR (negative IF score_samples > validation threshold AND abs robust z >= soft_z)',
        'data_assumption':'Caller supplies known-normal training and calibration periods; this is not automatically verified.'})
    print(f'TRAINED: {len(meta)} asset baselines. Model: {args.output / "sensor.joblib"}')


def infer(args):
    np,sklearn,joblib,IF=libs()
    if args.output.exists():raise ValueError('Run output folder exists; choose a new --output')
    rows=read_csv(args.csv)
    bundle=joblib.load(args.model) # only locally trained or otherwise trusted bundles
    tick=time.perf_counter();scored,episodes=score_rows(bundle,rows,args.persistence,args.hard_z,args.soft_z,args.max_gap_factor)
    args.output.mkdir(parents=True)
    args._output_created=True
    token=hashlib.sha256((digest(args.csv)+digest(args.model)+str((args.persistence,args.hard_z,args.soft_z,args.max_gap_factor))).encode()).hexdigest()[:16]
    events=episode_events(scored,episodes,args.csv,args.output,token)
    extra=load_events(args.merge_events) if args.merge_events else []
    result=fuse(events+extra)
    write_scores(args.output/'scored.csv',scored)
    for filename,items in [('sensor_events.jsonl',events),('combined_events.jsonl',events+extra)]:
        (args.output/filename).write_text(''.join(json.dumps(e.to_dict())+'\n' for e in items),encoding='utf-8')
    write_json(args.output/'incidents.json',result)
    if args.labels:write_json(args.output/'metrics.json',evaluate(scored,args.labels))
    write_json(args.output/'run_manifest.json',{'status':'completed','input_sha256':digest(args.csv),'model_sha256':digest(args.model),
        'measurements':len(scored),'candidate_samples':sum(r['candidate'] for r in scored),'alert_samples':sum(r['alert'] for r in scored),
        'sensor_episodes':len(events),'incidents':len(result['incidents']),'merged_events':len(extra),'persistence_samples':args.persistence,
        'hard_z':args.hard_z,'soft_z':args.soft_z,'max_gap_factor':args.max_gap_factor,'wall_seconds':time.perf_counter()-tick,
        'synthetic':any(r['synthetic'] for r in scored),'confidence_semantics':'1.0 is an adapter acceptance marker for a verified computed observation, NOT fault probability.',
        'limitations':['Clean-data assumption must be checked by operator.','Raw-feature baseline misses faults that remain near normal range.',
            'Batch replay, not real sensor streaming.','Observation timestamps are causal confirmation; episode evidence contains later context.']})
    print(f'COMPLETE: {len(scored)} measurements, {len(events)} sensor episodes, {len(result["incidents"])} fused incidents. Outputs: {args.output}')


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    t=sub.add_parser('train');t.add_argument('--train',type=Path,required=True);t.add_argument('--validation',type=Path,required=True)
    t.add_argument('--output',type=Path,default=Path('models/sensor-v1'));t.add_argument('--quantile',type=float,default=.995);t.add_argument('--seed',type=int,default=42)
    i=sub.add_parser('infer');i.add_argument('--csv',type=Path,required=True);i.add_argument('--model',type=Path,required=True);i.add_argument('--output',type=Path,default=Path('runs/sensor-01'))
    i.add_argument('--persistence',type=int,default=3);i.add_argument('--hard-z',type=float,default=6);i.add_argument('--soft-z',type=float,default=3)
    i.add_argument('--max-gap-factor',type=float,default=2);i.add_argument('--labels',type=Path);i.add_argument('--merge-events',type=Path)
    d=sub.add_parser('demo');d.add_argument('--output',type=Path,default=Path('data/sensor-demo'));d.add_argument('--seed',type=int,default=42)
    args=p.parse_args()
    args._output_created=False
    try:
        if args.command=='train':train(args)
        elif args.command=='infer':infer(args)
        else:
            from .sensor_demo import generate
            generate(args.output,args.seed)
    except (ValueError,OSError,RuntimeError,KeyError) as e:
        if args.command=='infer' and args._output_created:write_json(args.output/'run_manifest.json',{'status':'failed','error':str(e),'outputs_are_incomplete':True})
        print(f'ERROR: {e}',file=sys.stderr);raise SystemExit(1)

if __name__=='__main__':main()
