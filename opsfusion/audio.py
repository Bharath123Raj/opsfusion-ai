"""WAV -> log-mel statistics -> small PyTorch autoencoder -> evidence events."""
import argparse
import copy
import csv
from datetime import timedelta
import hashlib
import json
import math
from pathlib import Path
import sys
from .core import Event,fuse,parse_time
from .__main__ import write_json

SR=16000
BANDS=32
DIM=64


def deps():
    try:
        import numpy as np
        import torch
        from scipy.io import wavfile
        from scipy.signal import stft
        return np,torch,wavfile,stft
    except ImportError as e:raise RuntimeError('Install requirements-audio.txt using opsfusion312') from e


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def decode(path):
    np,torch,wavfile,stft=deps()
    sr,x=wavfile.read(path)
    if sr!=SR:raise ValueError('Audio must be 16000 Hz; convert explicitly before processing')
    if x.ndim!=1:raise ValueError('Audio must be mono; convert explicitly before processing')
    if x.dtype==np.int16:x=x.astype(np.float64)/32768
    elif np.issubdtype(x.dtype,np.floating):x=x.astype(np.float64)
    else:raise ValueError('Supported WAV formats: signed PCM16 or float32/64')
    if not np.all(np.isfinite(x)) or np.max(np.abs(x),initial=0)>1.001:raise ValueError('Audio contains invalid or out-of-range samples')
    if len(x)<SR:raise ValueError('At least one second of audio required')
    return x


def mel_filters():
    np,_,_,_=deps()
    hz=lambda m:700*(10**(m/2595)-1)
    edges=hz(np.linspace(2595*np.log10(1+40/700),2595*np.log10(1+7600/700),BANDS+2))
    freqs=np.fft.rfftfreq(512,1/SR)
    return np.array([np.maximum(0,np.minimum((freqs-a)/(b-a),(c-freqs)/(c-b))) for a,b,c in zip(edges,edges[1:],edges[2:])])


def features(path):
    np,torch,wavfile,stft=deps();x=decode(path);filters=mel_filters();vectors=[]
    for start in range(0,len(x)-SR+1,SR):
        _,_,z=stft(x[start:start+SR],fs=SR,nperseg=512,noverlap=256,boundary=None,padded=False)
        logmel=np.log10(np.maximum(filters@(np.abs(z)**2),1e-10))
        vectors.append(np.concatenate([logmel.mean(axis=1),logmel.std(axis=1)]))
    return np.array(vectors,dtype=np.float32),len(x)%SR


def network():
    _,torch,_,_=deps()
    return torch.nn.Sequential(torch.nn.Linear(DIM,32),torch.nn.ReLU(),torch.nn.Linear(32,8),torch.nn.ReLU(),
        torch.nn.Linear(8,32),torch.nn.ReLU(),torch.nn.Linear(32,DIM))


def read_manifest(path):
    manifest=json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('schema')!='opsfusion_audio_manifest_v1':raise ValueError('Unsupported audio manifest')
    records=[];hashes=set()
    for r in manifest['clips']:
        if set(r)!= {'path','asset_id','start_time','synthetic'}:raise ValueError('Audio clip fields must be path, asset_id, start_time, synthetic; labels belong elsewhere')
        if type(r['synthetic']) is not bool or not isinstance(r['asset_id'],str) or not r['asset_id'].strip():raise ValueError('Invalid clip provenance or asset')
        start=parse_time(r['start_time']);clip=(path.parent/r['path']).resolve()
        if not clip.is_file():raise ValueError(f'Clip not found: {clip}')
        h=sha(clip)
        if h in hashes:raise ValueError('Duplicate audio content in manifest')
        hashes.add(h);vectors,tail=features(clip)
        records.append({'path':clip,'asset_id':r['asset_id'],'start':start,'end':start+timedelta(seconds=len(vectors)+tail/SR),
            'synthetic':r['synthetic'],'sha256':h,'vectors':vectors,'discarded_tail_samples':tail})
    if not records:raise ValueError('No audio clips')
    for asset in {r['asset_id'] for r in records}:
        ordered=sorted([r for r in records if r['asset_id']==asset],key=lambda r:r['start'])
        if any(a['end']>b['start'] for a,b in zip(ordered,ordered[1:])):raise ValueError('Same-asset clips overlap')
    return records


def fit(train,val,seed=42,epochs=200,quantile=.99):
    np,torch,_,_=deps()
    if not .9<=quantile<=1 or not 10<=epochs<=2000:raise ValueError('Require quantile in [.9,1] and epochs in [10,2000]')
    assets={r['asset_id'] for r in train}
    if assets!={r['asset_id'] for r in val}:raise ValueError('Training and calibration assets must match')
    if {r['sha256'] for r in train}&{r['sha256'] for r in val}:raise ValueError('Training/calibration audio content overlaps')
    torch.set_num_threads(1);models={};meta={}
    for asset in sorted(assets):
        t=[r for r in train if r['asset_id']==asset];v=[r for r in val if r['asset_id']==asset]
        if max(r['end'] for r in t)>min(r['start'] for r in v):raise ValueError('Training clips must end before calibration begins')
        for split in (t,v):
            ordered=sorted(split,key=lambda r:r['start'])
            if any(a['end']>b['start'] for a,b in zip(ordered,ordered[1:])):raise ValueError('Same-asset clips overlap within a split')
        x=np.concatenate([r['vectors'] for r in t]);y=np.concatenate([r['vectors'] for r in v])
        if len(x)<24 or len(y)<12:raise ValueError('Need at least 24 train and 12 calibration one-second windows per asset')
        center=x.mean(axis=0);scale=np.maximum(x.std(axis=0),.05)
        tx=torch.tensor((x-center)/scale);vy=torch.tensor((y-center)/scale)
        torch.manual_seed(seed);model=network();optimizer=torch.optim.Adam(model.parameters(),lr=.001)
        best=float('inf');best_state=None;wait=0;history=[]
        for epoch in range(epochs):
            model.train();optimizer.zero_grad();loss=((model(tx)-tx)**2).mean();loss.backward();optimizer.step()
            model.eval()
            with torch.no_grad():val_loss=float(((model(vy)-vy)**2).mean())
            history.append({'epoch':epoch+1,'train_loss':float(loss.detach()),'validation_loss':val_loss})
            if val_loss<best-1e-5:best=val_loss;best_state=copy.deepcopy(model.state_dict());wait=0
            else:wait+=1
            if wait>=20:break
        model.load_state_dict(best_state);model.eval()
        with torch.no_grad():scores=((model(vy)-vy)**2).mean(dim=1).numpy()
        threshold=float(np.quantile(scores,quantile))
        models[asset]={'state_dict':best_state,'center':torch.tensor(center),'scale':torch.tensor(scale),'threshold':threshold,
            'calibration_end':max(r['end'] for r in v).isoformat(),'known_hashes':[r['sha256'] for r in t+v],
            'trained_synthetic':any(r['synthetic'] for r in t+v)}
        meta[asset]={'train_windows':len(x),'calibration_windows':len(y),'threshold':threshold,'epochs_run':len(history),
            'best_validation_loss':best,'history':history,'calibration_end':models[asset]['calibration_end'],
            'train_sha256':[r['sha256'] for r in t],'validation_sha256':[r['sha256'] for r in v],
            'trained_synthetic':models[asset]['trained_synthetic']}
    return {'format':'opsfusion_audio_ae_v1','sample_rate':SR,'feature_dim':DIM,'bands':BANDS,'seed':seed,
        'torch_version':str(torch.__version__),'models':models},meta


def score(bundle,record,persistence=2):
    np,torch,_,_=deps()
    if type(persistence)!=int or persistence<1:raise ValueError('persistence must be positive integer')
    if bundle.get('format')!='opsfusion_audio_ae_v1':raise ValueError('Unknown audio model')
    if record['asset_id'] not in bundle['models']:raise ValueError('Unknown asset: train this machine first')
    m=bundle['models'][record['asset_id']]
    if record['sha256'] in m['known_hashes']:raise ValueError('Inference clip duplicates training/calibration content')
    if record['start']<parse_time(m['calibration_end']):raise ValueError('Inference must start after calibration ends')
    model=network();model.load_state_dict(m['state_dict']);model.eval()
    x=torch.tensor(record['vectors']);z=(x-m['center'])/m['scale']
    with torch.no_grad():scores=((model(z)-z)**2).mean(dim=1).numpy()
    rows=[];episodes=[];streak=0;active=None
    for idx,s in enumerate(scores):
        candidate=bool(s>m['threshold']);streak=streak+1 if candidate else 0
        alert=streak>=persistence
        row={'asset_id':record['asset_id'],'window_start_seconds':idx,'window_end_seconds':idx+1,'score':float(s),
            'threshold':m['threshold'],'candidate':candidate,'alert':alert,
            'available_at':(record['start']+timedelta(seconds=idx+1)).isoformat(),
            'synthetic':bool(record['synthetic'] or m['trained_synthetic'])}
        rows.append(row)
        if alert:
            if active is None:active={'onset_window':idx-persistence+1,'confirmation_window':idx,'end_window':idx}
            else:active['end_window']=idx
        elif active is not None:episodes.append(active);active=None
    if active is not None:episodes.append(active)
    return rows,episodes


def train_command(args):
    np,torch,_,_=deps()
    if args.output.exists():raise ValueError('Model folder exists; choose a new --output')
    t,v=read_manifest(args.train),read_manifest(args.validation)
    bundle,metadata=fit(t,v,args.seed,args.epochs,args.quantile)
    args.output.mkdir(parents=True);torch.save(bundle,args.output/'audio.pt')
    write_json(args.output/'training_manifest.json',{'architecture':'64 -> 32 -> 8 -> 32 -> 64 dense autoencoder; ReLU; MSE; Adam',
        'feature_method':'32-band log-mel time mean and standard deviation in nonoverlapping one-second windows',
        'seed':args.seed,'quantile':args.quantile,'torch_version':str(torch.__version__),'assets':metadata,
        'assumption':'Caller supplies known-normal training/calibration recordings; not automatically verified'})
    print(f'TRAINED AUDIO: {len(metadata)} asset baselines. Model: {args.output / "audio.pt"}')


def infer_command(args):
    np,torch,_,_=deps()
    if args.output.exists():raise ValueError('Run folder exists; choose a new --output')
    records=read_manifest(args.manifest)
    bundle=torch.load(args.model,map_location='cpu',weights_only=True)
    scored=[(r,*score(bundle,r,args.persistence)) for r in records]
    args.output.mkdir(parents=True);args.created=True;(args.output/'evidence').mkdir();events=[];allrows=[]
    for record,rows,episodes in scored:
        token=hashlib.sha256((record['sha256']+sha(args.model)+record['asset_id']+record['start'].isoformat()+str(args.persistence)).encode()).hexdigest()[:16]
        for n,e in enumerate(episodes,1):
            event_id=f'audio-{token}-{n}';path=args.output/'evidence'/f'{event_id}.json'
            write_json(path,{'source_wav':record['path'].as_uri(),'source_sha256':record['sha256'],'episode':e,
                'scores':rows[e['onset_window']:e['end_window']+1],'note':'Completed episode context; alert time is when confirming full window is available'})
            snapshot=args.output/'evidence'/f'{event_id}.wav';x=decode(record['path'])
            clip=x[e['onset_window']*SR:(e['end_window']+1)*SR]
            from scipy.io import wavfile
            wavfile.write(snapshot,SR,(clip*32767).astype(np.int16))
            confirmed=rows[e['confirmation_window']]
            events.append(Event(event_id,confirmed['available_at'],record['asset_id'],'audio','abnormal_noise',f'{record["asset_id"]}-microphone',path.resolve().as_uri(),
                f'Persistent acoustic feature deviation; WAV offsets {e["onset_window"]}–{e["end_window"]+1}s; reconstruction error {confirmed["score"]:.4f} above {confirmed["threshold"]:.4f}. Cause unconfirmed.',1.,confirmed['synthetic']))
        allrows.extend({**r,'clip_sha256':record['sha256']} for r in rows)
    with (args.output/'scores.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(allrows[0]));writer.writeheader();writer.writerows(allrows)
    (args.output/'audio_events.jsonl').write_text(''.join(json.dumps(e.to_dict())+'\n' for e in events),encoding='utf-8')
    write_json(args.output/'incidents.json',fuse(events,standalone_audio_logs=True))
    write_json(args.output/'run_manifest.json',{'status':'completed','audio_windows':len(allrows),'audio_episodes':len(events),
        'candidate_windows':sum(r['candidate'] for r in allrows),'alert_windows':sum(r['alert'] for r in allrows),'persistence_windows':args.persistence,
        'discarded_tail_samples':sum(r['discarded_tail_samples'] for r in records),'synthetic':any(r['synthetic'] for r in allrows),
        'confidence_semantics':'1.0 is an adapter acceptance marker, not fault probability',
        'limitations':['Generated demo tones are not real machine recordings.','Mic placement, gain, background noise and operating load affect scores.',
            'Fixed nonoverlapping windows; independent recordings reset persistence.','Acoustic deviation is not a diagnosis.']})
    print(f'COMPLETE AUDIO: {len(allrows)} windows, {len(events)} confirmed episodes. Outputs: {args.output}')


def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='command',required=True)
    d=s.add_parser('demo');d.add_argument('--output',type=Path,default=Path('data/audio-demo'));d.add_argument('--seed',type=int,default=42)
    t=s.add_parser('train');t.add_argument('--train',type=Path,required=True);t.add_argument('--validation',type=Path,required=True)
    t.add_argument('--output',type=Path,default=Path('models/audio-v1'));t.add_argument('--seed',type=int,default=42);t.add_argument('--epochs',type=int,default=200);t.add_argument('--quantile',type=float,default=.99)
    i=s.add_parser('infer');i.add_argument('--manifest',type=Path,required=True);i.add_argument('--model',type=Path,required=True)
    i.add_argument('--output',type=Path,default=Path('runs/audio-01'));i.add_argument('--persistence',type=int,default=2)
    args=p.parse_args();args.created=False
    try:
        if args.command=='train':train_command(args)
        elif args.command=='infer':infer_command(args)
        else:
            from .audio_demo import generate
            generate(args.output,args.seed)
    except (ValueError,RuntimeError,OSError,KeyError) as e:
        if args.created:write_json(args.output/'run_manifest.json',{'status':'failed','error':str(e),'outputs_are_incomplete':True})
        print(f'ERROR: {e}',file=sys.stderr);raise SystemExit(1)

if __name__=='__main__':main()
