"""Compare fixed windows and quiet-gap episodes on identical events and labels."""
import argparse,csv,hashlib,json
from pathlib import Path
from .replay import replay,save_replay
from .incident_eval import evaluate
from .__main__ import load_events,write_json


def compare(events,quiet_gap_seconds=20):
    return {engine:{'replay':replay(events,engine=engine,quiet_gap_seconds=quiet_gap_seconds)} for engine in ('fixed','episode')}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--events',type=Path,required=True);p.add_argument('--labels',type=Path,required=True)
    p.add_argument('--output',type=Path,default=Path('runs/segmentation-compare-01'));p.add_argument('--quiet-gap-seconds',type=float,default=20)
    p.add_argument('--gap-sweep',type=float,nargs='*',default=[]);a=p.parse_args()
    if a.output.exists():p.error('Output folder exists; choose a new --output')
    try:
        events=load_events(a.events);labels=json.loads(a.labels.read_text());results=compare(events,a.quiet_gap_seconds)
        for result in results.values():result['metrics']=evaluate(result['replay'],labels)
        sweep=[]
        for gap in a.gap_sweep:
            r=replay(events,engine='episode',quiet_gap_seconds=gap);m=evaluate(r,labels)
            sweep.append({'quiet_gap_seconds':gap,'run_id':r['run_id'],**{k:m[k] for k in ('tp','fp','fn','precision','recall','f1')}})
    except (ValueError,OSError,KeyError) as e:p.error(str(e))
    a.output.mkdir(parents=True)
    rows=[]
    for engine,result in results.items():
        save_replay(result['replay'],a.output/engine)
        write_json(a.output/engine/'metrics.json',result['metrics'])
        rows.append({'engine':engine,'quiet_gap_seconds':a.quiet_gap_seconds if engine=='episode' else None,
            **{k:result['metrics'][k] for k in ('tp','fp','fn','precision','recall','f1','matched_detection_delay_median_seconds')}})
    with (a.output/'comparison.csv').open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    summary={'scope':labels['scope'],'synthetic':labels['synthetic'],'events_sha256':hashlib.sha256(a.events.read_bytes()).hexdigest(),
        'labels_sha256':hashlib.sha256(a.labels.read_bytes()).hexdigest(),'quiet_gap_seconds':a.quiet_gap_seconds,
        'results':{engine:{'run_id':r['replay']['run_id'],'metrics':r['metrics']} for engine,r in results.items()},'sensitivity_sweep':sweep,
        'note':'Same input and labels for both engines. A quiet gap is absence of adapter observations, not verified physical recovery. Sweep is descriptive, not automatic parameter selection.'}
    write_json(a.output/'summary.json',summary)
    for row in rows:print(f"{row['engine']}: TP={row['tp']}, FP={row['fp']}, FN={row['fn']}, F1={row['f1']}")
    print(f'COMPLETE SEGMENTATION COMPARISON: 2 engines, identical inputs and labels. Outputs: {a.output}')

if __name__=='__main__':main()
