"""Optional evaluation reads labels only after inference has completed."""
import argparse,csv,json
from pathlib import Path
from .__main__ import write_json

def evaluate(scores_path,labels_path):
    labels=json.loads(labels_path.read_text(encoding='utf-8'))
    if labels.get('schema')!='opsfusion_audio_labels_v1':raise ValueError('Wrong audio label schema')
    rows=list(csv.DictReader(scores_path.open(encoding='utf-8')))
    if len(rows)!=labels['windows'] or any(r['asset_id']!=labels['asset_id'] for r in rows):raise ValueError('Demo labels require one clip with matching asset/window coverage')
    from .core import parse_time
    from datetime import timedelta
    start=parse_time(labels['start_time']);seen=set();tp=fp=tn=fn=0
    for row in rows:
        index=int(row['window_start_seconds'])
        if index in seen or float(row['window_end_seconds'])!=index+1:raise ValueError('Invalid window coverage')
        seen.add(index)
        if parse_time(row['available_at'])!=start+timedelta(seconds=index+1):raise ValueError('Labels and clip anchor mismatch')
        truth=any(a<=index and index+1<=b for a,b in labels['fault_intervals_seconds'])
        pred=row['alert'].lower()=='true'
        if truth and pred:tp+=1
        elif pred:fp+=1
        elif truth:fn+=1
        else:tn+=1
    if seen!=set(range(labels['windows'])):raise ValueError('Missing windows')
    safe=lambda a,b:a/b if b else None
    return {'scope':labels['scope'],'synthetic':labels['synthetic'],'tp':tp,'fp':fp,'tn':tn,'fn':fn,
        'precision':safe(tp,tp+fp),'recall':safe(tp,tp+fn),'matching':'Whole one-second window inside labelled deviation interval',
        'note':'Synthetic point-window evaluation; not cause classification or factory accuracy.'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--scores',type=Path,required=True);p.add_argument('--labels',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():p.error('Output exists; choose another filename')
    try:result=evaluate(a.scores,a.labels)
    except (ValueError,OSError,KeyError) as e:p.error(str(e))
    write_json(a.output,result);print(json.dumps(result,indent=2))
if __name__=='__main__':main()
