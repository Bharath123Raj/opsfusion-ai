"""Modality ablations over a fixed independently labelled adapter-event corpus."""
import argparse,csv,json
from pathlib import Path
from .__main__ import load_events,write_json
from .replay import replay
from .incident_eval import evaluate
from .incident_demo import generate

VARIANTS={'all':None,'sensor_only':{'sensor'},'video_only':{'video'},'audio_only':{'audio'},'log_only':{'log'},
    'without_audio':{'sensor','video','log','maintenance','note'},'without_video':{'sensor','audio','log','maintenance','note'},
    'without_log':{'sensor','audio','video','maintenance','note'},'audio_clock_plus30s':None}


def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='command',required=True)
    d=s.add_parser('demo');d.add_argument('--output',type=Path,default=Path('data/incident-demo'))
    r=s.add_parser('run');r.add_argument('--events',type=Path,required=True);r.add_argument('--labels',type=Path,required=True)
    r.add_argument('--output',type=Path,default=Path('runs/incident-benchmark-01'));a=p.parse_args()
    if a.output.exists():p.error('Output folder exists; choose a new --output')
    try:
        if a.command=='demo':generate(a.output);return
        events=load_events(a.events);labels=json.loads(a.labels.read_text());results={}
        for name,modalities in VARIANTS.items():
            selected=events if modalities is None else [e for e in events if e.modality in modalities]
            if name=='audio_clock_plus30s':
                from dataclasses import replace
                from datetime import timedelta
                selected=[replace(e,timestamp=(e.time+timedelta(seconds=30)).isoformat()) if e.modality=='audio' else e for e in selected]
            run=replay(selected);metrics=evaluate(run,labels)
            results[name]={'replay_run_id':run['run_id'],'observations':len(selected),'first_alerts_all_categories':len(run['first_alerts']),'metrics':metrics}
        a.output.mkdir(parents=True)
        for name,result in results.items():write_json(a.output/f'{name}.json',result)
        with (a.output/'ablation.csv').open('w',newline='',encoding='utf-8') as f:
            fields=['variant','observations','tp','fp','fn','precision','recall','f1','matched_detection_delay_median_seconds','false_alerts_per_normal_asset_hour']
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
            for name,r in results.items():w.writerow({'variant':name,'observations':r['observations'],**{k:r['metrics'][k] for k in fields[2:]}})
        import hashlib
        write_json(a.output/'summary.json',{'events_sha256':hashlib.sha256(a.events.read_bytes()).hexdigest(),
            'labels_sha256':hashlib.sha256(a.labels.read_bytes()).hexdigest(),'scope':labels['scope'],'synthetic':labels['synthetic'],'results':results,
            'note':'Fixed fusion-rule modality ablations on adapter events; not an independent perception-model benchmark.'})
        print(f'COMPLETE BENCHMARK: {len(results)} variants. All-source TP={results["all"]["metrics"]["tp"]}, FP={results["all"]["metrics"]["fp"]}, FN={results["all"]["metrics"]["fn"]}. Outputs: {a.output}')
    except (ValueError,OSError,KeyError) as e:p.error(str(e))

if __name__=='__main__':main()
