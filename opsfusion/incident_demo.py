"""Authored incident scenarios; labels are independent of fusion predictions."""
from datetime import datetime,timedelta,timezone
from .core import Event
from .__main__ import write_json
import json


def generate(output):
    if output.exists():raise ValueError('Dataset folder exists; choose a new output')
    output.mkdir(parents=True);anchor=datetime(2026,1,1,12,0,tzinfo=timezone.utc)
    # Each expected incident is authored here, never obtained by invoking fuse().
    cases=[
        ('degradation_three_sources',[(0,'sensor','sensor_anomaly',1),(4,'audio','abnormal_noise',1),(7,'log','motor_warning',1)],[(0,20,'machine_degradation')]),
        ('zone_crossing',[(0,'video','zone_entry',1)],[(0,10,'restricted_zone')]),
        ('shutdown_after_sensor_warning',[(0,'sensor','sensor_anomaly',1),(12,'log','shutdown',1)],[(0,20,'abnormal_shutdown')]),
        ('degradation_then_shutdown',[(0,'sensor','sensor_anomaly',1),(5,'log','motor_warning',1),(15,'log','shutdown',1)],[(0,20,'machine_degradation'),(0,20,'abnormal_shutdown')]),
        ('audio_log_pair',[(0,'audio','abnormal_noise',1),(6,'log','motor_warning',1)],[(0,20,'machine_degradation')]),
        ('repeated_single_modality',[(0,'sensor','sensor_anomaly',1),(10,'sensor','sensor_anomaly',1),(20,'sensor','sensor_anomaly',1)],[]),
        ('too_far_apart',[(0,'sensor','sensor_anomaly',1),(120,'audio','abnormal_noise',1)],[]),
        ('low_quality_audio',[(0,'sensor','sensor_anomaly',1),(4,'audio','abnormal_noise',.2)],[]),
        ('separate_assets',[(0,'sensor','sensor_anomaly',1),(4,'audio','abnormal_noise',1)],[]),
        ('two_distinct_faults_inside_90s',[(0,'sensor','sensor_anomaly',1),(4,'audio','abnormal_noise',1),(40,'sensor','sensor_anomaly',1),(44,'audio','abnormal_noise',1)],[(0,20,'machine_degradation'),(40,60,'machine_degradation')]),
    ]
    events=[];labels=[];coverage=[];stories=[]
    for index,(name,signals,expected) in enumerate(cases):
        start=anchor+timedelta(seconds=index*180);asset=f'asset-{index:02d}'
        for offset,modality,kind,confidence in signals:
            entity=asset+'-peer' if name=='separate_assets' and modality=='audio' else asset
            eid=f'case{index:02d}-{len(events):03d}'
            events.append(Event(eid,(start+timedelta(seconds=offset)).isoformat(),entity,modality,kind,f'{entity}-{modality}',
                f'synthetic://incident-demo/{eid}',f'Authored synthetic {kind} observation',confidence,True))
        for number,(begin,end,kind) in enumerate(expected):
            labels.append({'label_id':f'label-{index:02d}-{number}','asset_id':asset,'type':kind,
                'start':(start+timedelta(seconds=begin)).isoformat(),'end':(start+timedelta(seconds=end)).isoformat()})
        coverage.append({'asset_id':asset,'start':start.isoformat(),'end':(start+timedelta(seconds=151)).isoformat(),'normal':not bool(expected)})
        if name=='separate_assets':coverage.append({'asset_id':asset+'-peer','start':start.isoformat(),'end':(start+timedelta(seconds=151)).isoformat(),'normal':True})
        stories.append({'case':name,'asset_id':asset,'description':'Two distinct faults 40 seconds apart intentionally test the fixed fusion window limit.' if name=='two_distinct_faults_inside_90s' else name.replace('_',' ')})
    (output/'events.jsonl').write_text(''.join(json.dumps(e.to_dict())+'\n' for e in events),encoding='utf-8')
    write_json(output/'labels.json',{'schema':'opsfusion_incident_labels_v1','scope':'Ten authored synthetic adapter-event scenarios; downstream fusion benchmark only',
        'synthetic':True,'evaluation_types':['machine_degradation','restricted_zone','abnormal_shutdown'],'incidents':labels,'coverage':coverage})
    write_json(output/'source.json',{'synthetic':True,'cases':stories,'events':len(events),'labelled_incidents':len(labels),
        'note':'No video/audio/sensor model inference occurs in this benchmark. Labels do not prove physical causes.'})
    print(f'INCIDENT DEMO: {len(events)} observations, {len(labels)} independent incident labels in 10 scenarios. Folder: {output}')
