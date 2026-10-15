"""Synthetic raw measurements; fault labels are separate from inference input."""
import csv
from datetime import datetime,timedelta,timezone
from pathlib import Path
import random
from .sensor import FIELDS
from .__main__ import write_json


def generate(output,seed=42):
    if output.exists():raise ValueError('Demo output folder exists; choose a new --output')
    output.mkdir(parents=True)
    rng=random.Random(seed);anchor=datetime(2026,1,1,10,0,tzinfo=timezone.utc)
    assets=[('warehouse-zone-01',2.0,55.0),('machine-b',3.5,62.0)]
    labels=[];intervals=[]
    fault_windows={'warehouse-zone-01':[(4,16,'vibration_bias'),(80,110,'temperature_bias'),(180,230,'multivariate_shift'),(260,261,'single_sample_spike')],
        'machine-b':[(120,140,'vibration_bias'),(300,350,'temperature_drift')]}
    for split,count,offset in [('train',800,-1000),('validation',200,-200),('test',400,0)]:
        with (output/f'{split}.csv').open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader()
            for asset,vcenter,tcenter in assets:
                for j in range(count):
                    timestamp=(anchor+timedelta(seconds=offset+j)).isoformat()
                    v=max(0,rng.gauss(vcenter,.12));temp=rng.gauss(tcenter,.5);fault=None
                    if split=='test':
                        for begin,end,kind in fault_windows[asset]:
                            if begin<=j<end:
                                fault=kind
                                if kind in ('vibration_bias','single_sample_spike','multivariate_shift'):v+=2.5
                                if kind in ('temperature_bias','multivariate_shift'):temp+=8
                                if kind=='temperature_drift':temp+=(j-begin)*.25
                        labels.append({'asset_id':asset,'timestamp':timestamp,'anomaly':fault is not None,'fault_type':fault})
                    writer.writerow({'timestamp':timestamp,'asset_id':asset,'vibration_rms_mm_s':round(v,5),'temperature_c':round(temp,5),'synthetic':'true'})
    for asset,windows in fault_windows.items():
        intervals.extend({'asset_id':asset,'start':(anchor+timedelta(seconds=b)).isoformat(),'end_exclusive':(anchor+timedelta(seconds=e)).isoformat(),'fault_type':kind} for b,e,kind in windows)
    write_json(output/'labels.json',{'schema':'opsfusion_sensor_labels_v1','synthetic':True,
        'scope':'Seeded synthetic injected-fault measurements; demonstration only, not real machinery accuracy',
        'point_labels':labels,'fault_intervals':intervals})
    write_json(output/'source.json',{'source':'opsfusion synthetic measurement generator','seed':seed,'synthetic':True,
        'assets':[a[0] for a in assets],'cadence_seconds':1,'train_rows':1600,'validation_rows':400,'test_rows':800,
        'features_units':{'vibration_rms_mm_s':'mm/s','temperature_c':'degrees Celsius'},
        'test_anchor':anchor.isoformat(),'faults_in_train_or_validation':False,
        'note':'Overlapping public video replay and these generated sensors is a composed synthetic demonstration, not a real joint incident.'})
    print(f'DEMO DATA: 1600 training, 400 validation, 800 test measurements. Synthetic labels: {output / "labels.json"}')
