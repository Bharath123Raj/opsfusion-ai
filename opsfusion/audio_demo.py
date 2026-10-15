"""Generated WAVs for reproducible signal-processing checks, not factory data."""
from datetime import datetime,timedelta,timezone
import csv
import json
from .audio import deps,SR
from .__main__ import write_json


def generate(output,seed=42):
    if output.exists():raise ValueError('Demo directory exists; choose a new --output')
    np,torch,wavfile,stft=deps();output.mkdir(parents=True);rng=np.random.default_rng(seed)
    anchor=datetime(2026,1,1,10,0,tzinfo=timezone.utc);asset='warehouse-zone-01'
    for split,count,length,offset in [('train',4,12,-200),('validation',2,12,-100),('test',1,30,0)]:
        records=[]
        for i in range(count):
            time=np.arange(length*SR)/SR;frequency=120+rng.uniform(-1,1)
            signal=.18*np.sin(2*np.pi*frequency*time)+.04*np.sin(2*np.pi*2*frequency*time)+.003*rng.normal(size=len(time))
            if split=='test':
                mask=(time>=4)&(time<10);signal[mask]+=.08*np.sin(2*np.pi*1800*time[mask])+.025*rng.normal(size=mask.sum())
                mask=(time>=18)&(time<24);signal[mask]+=.1*np.sin(2*np.pi*(700*time[mask]+25*time[mask]**2))
            filename=f'{split}-{i}.wav';wavfile.write(output/filename,SR,(np.clip(signal,-1,1)*32767).astype(np.int16))
            records.append({'path':filename,'asset_id':asset,'start_time':(anchor+timedelta(seconds=offset+i*15)).isoformat(),'synthetic':True})
        write_json(output/f'{split}.json',{'schema':'opsfusion_audio_manifest_v1','clips':records})
    write_json(output/'labels.json',{'schema':'opsfusion_audio_labels_v1','synthetic':True,
        'scope':'Generated harmonics and injected spectral changes; not real machinery accuracy',
        'asset_id':asset,'start_time':anchor.isoformat(),'fault_intervals_seconds':[[4,10],[18,24]],'windows':30})
    with (output/'logs.csv').open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['timestamp','asset_id','source_id','level','code','message','synthetic']);writer.writeheader()
        for sec,level,code,message in [(0,'INFO','HEARTBEAT','Controller connected'),(7,'WARN','TEMP_WARNING','Temperature warning reported'),
            (12,'INFO','HEARTBEAT','Controller running'),(20,'ERROR','VIBRATION_WARNING','Vibration warning reported'),
            (22,'CRITICAL','UNMAPPED_CODE','Unknown code retained for review'),(26,'ERROR','PROTECTIVE_SHUTDOWN','Protective stop reported')]:
            writer.writerow({'timestamp':(anchor+timedelta(seconds=sec)).isoformat(),'asset_id':asset,'source_id':'controller-01',
                'level':level,'code':code,'message':message,'synthetic':'true'})
    write_json(output/'source.json',{'synthetic':True,'seed':seed,'generator':'OpsFusion harmonics + injected spectral deviations',
        'sample_rate':SR,'train_seconds':48,'calibration_seconds':24,'test_seconds':30,
        'note':'Generated hums are not recordings of bearing faults. Logs are synthetic controller fixtures.'})
    print(f'AUDIO DEMO: 7 generated mono WAV files and 6 synthetic controller records. Folder: {output}')
