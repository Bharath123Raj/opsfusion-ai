"""Separate authored regression scenarios, not blind or real-data holdout."""
from datetime import datetime,timedelta,timezone
import json
from .core import Event
from .__main__ import write_json


def generate(output):
    if output.exists():raise ValueError('Output exists; choose another folder')
    output.mkdir(parents=True);anchor=datetime(2026,1,2,12,tzinfo=timezone.utc)
    cases=[
        ('continuous_over_90s',[(0,'sensor','sensor_anomaly'),(4,'audio','abnormal_noise')]+[(i,'sensor','sensor_anomaly') for i in range(10,161,10)]+[(104,'audio','abnormal_noise')],[(0,180,'machine_degradation')]),
        ('short_gap_same_episode',[(0,'sensor','sensor_anomaly'),(4,'audio','abnormal_noise'),(16,'sensor','sensor_anomaly'),(19,'audio','abnormal_noise')],[(0,30,'machine_degradation')]),
        ('two_faults_with_quiet_interval',[(0,'sensor','sensor_anomaly'),(4,'audio','abnormal_noise'),(55,'sensor','sensor_anomaly'),(60,'audio','abnormal_noise')],[(0,20,'machine_degradation'),(55,75,'machine_degradation')]),
        ('notes_do_not_bridge_recovered_warning',[(0,'sensor','sensor_anomaly')]+[(i,'note','operator_note') for i in range(10,51,10)]+[(54,'audio','abnormal_noise')],[]),
        ('safety_activity_does_not_bridge_machine_fault',[(0,'sensor','sensor_anomaly'),(10,'video','zone_entry'),(20,'video','zone_entry'),(30,'video','zone_entry'),(40,'audio','abnormal_noise')],[(10,35,'restricted_zone')]),
        ('old_warning_unrelated_to_stop',[(0,'sensor','sensor_anomaly'),(10,'note','operator_note'),(20,'note','operator_note'),(40,'log','shutdown')],[]),
        ('sparse_continuing_fault_ambiguous',[(0,'sensor','sensor_anomaly'),(4,'audio','abnormal_noise'),(45,'sensor','sensor_anomaly'),(49,'audio','abnormal_noise')],[(0,70,'machine_degradation')]),
        ('slow_cross_source_confirmation',[(0,'sensor','sensor_anomaly'),(25,'audio','abnormal_noise')],[(0,35,'machine_degradation')]),
    ]
    events=[];truth=[];coverage=[];stories=[]
    for index,(name,signals,expected) in enumerate(cases):
        start=anchor+timedelta(seconds=index*240);asset=f'regression-{index:02d}'
        for number,(offset,modality,kind) in enumerate(sorted(signals)):
            eid=f'regression-{index:02d}-{number:02d}'
            events.append(Event(eid,(start+timedelta(seconds=offset)).isoformat(),asset,modality,kind,modality,
                f'synthetic://segmentation-regression/{eid}','Authored synthetic observation',1.,True))
        for j,(begin,end,kind) in enumerate(expected):
            truth.append({'label_id':f'regression-label-{index:02d}-{j}','asset_id':asset,'type':kind,
                'start':(start+timedelta(seconds=begin)).isoformat(),'end':(start+timedelta(seconds=end)).isoformat()})
        coverage.append({'asset_id':asset,'start':start.isoformat(),'end':(start+timedelta(seconds=210)).isoformat(),'normal':not bool(expected)})
        stories.append({'case':name,'asset_id':asset,
            'annotation_assumption':'Scenario author states whether the fault continues or recovers; sparse observations alone cannot establish this.'})
    (output/'events.jsonl').write_text(''.join(json.dumps(e.to_dict())+'\n' for e in events),encoding='utf-8')
    write_json(output/'labels.json',{'schema':'opsfusion_incident_labels_v1','scope':'Eight separate authored segmentation regression scenarios; not blind holdout or real-data accuracy',
        'synthetic':True,'evaluation_types':['machine_degradation','restricted_zone','abnormal_shutdown'],'incidents':truth,'coverage':coverage})
    write_json(output/'source.json',{'synthetic':True,'cases':stories,'events':len(events),'labels':len(truth),
        'note':'Constructed to probe the new heuristic, including its sparse-warning and slow-corroboration failure modes. Not independent blinded validation.'})
    print(f'SEGMENTATION REGRESSION: {len(events)} observations, {len(truth)} labels, {len(cases)} cases. Folder: {output}')
