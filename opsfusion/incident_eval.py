"""Incident-level evaluation of immutable first alerts against independent labels."""
import argparse,json,math
from pathlib import Path
from .core import parse_time
from .__main__ import write_json


def validate_labels(labels):
    if labels.get('schema')!='opsfusion_incident_labels_v1':raise ValueError('Wrong incident label schema')
    types=labels['evaluation_types']
    if not isinstance(types,list) or not types or len(types)!=len(set(types)):raise ValueError('Evaluation types must be a unique nonempty list')
    known=set()
    for row in labels['incidents']:
        if not row['label_id'] or row['label_id'] in known:raise ValueError('Invalid or duplicate label ID')
        known.add(row['label_id'])
        if not row['asset_id'] or row['type'] not in types:raise ValueError('Invalid labelled asset or type')
        if parse_time(row['end'])<parse_time(row['start']):raise ValueError('Label end before start')
    exposures={}
    for row in labels['coverage']:
        start,end=parse_time(row['start']),parse_time(row['end'])
        if end<=start:raise ValueError('Coverage must have positive duration')
        if type(row['normal']) is not bool:raise ValueError('Coverage normal flag must be boolean')
        exposures.setdefault(row['asset_id'],[]).append((start,end,row['normal']))
    for rows in exposures.values():
        rows.sort()
        if any(a[1]>b[0] for a,b in zip(rows,rows[1:])):raise ValueError('Coverage intervals overlap for an asset')
    for row in labels['incidents']:
        if any(n and s<=parse_time(row['start'])<e for s,e,n in exposures.get(row['asset_id'],[])):
            raise ValueError('Incident label conflicts with normal coverage')
        if not any(s<=parse_time(row['start']) and parse_time(row['end'])<=e for s,e,n in exposures.get(row['asset_id'],[])):
            raise ValueError('Label lies outside declared coverage')
    return exposures


def evaluate(result,labels,tolerance_seconds=0):
    exposures=validate_labels(labels)
    if not math.isfinite(tolerance_seconds) or tolerance_seconds<0:raise ValueError('Tolerance must be nonnegative and finite')
    from datetime import timedelta
    predictions=[a for a in result['first_alerts'] if a['type'] in labels['evaluation_types']]
    truth=labels['incidents'];edges={}
    ids=set()
    for i,p in enumerate(predictions):
        if p['alert_id'] in ids:raise ValueError('Duplicate first alert ID')
        ids.add(p['alert_id']);at=parse_time(p['first_emitted_at'])
        if not any(s<=at<e for s,e,n in exposures.get(p['asset_id'],[])):raise ValueError('Prediction lies outside label coverage; labels cannot score this run')
        candidates=[]
        for j,t in enumerate(truth):
            if p['asset_id']==t['asset_id'] and p['type']==t['type'] and parse_time(t['start'])<=at<=parse_time(t['end'])+timedelta(seconds=tolerance_seconds):
                candidates.append(j)
        edges[i]=sorted(candidates,key=lambda j:((at-parse_time(truth[j]['start'])).total_seconds(),truth[j]['label_id']))
    # Maximum-cardinality bipartite matching: duplicate alerts cannot share a label.
    owners={}
    def augment(i,visited):
        for j in edges[i]:
            if j in visited:continue
            visited.add(j)
            if j not in owners or augment(owners[j],visited):owners[j]=i;return True
        return False
    for i in sorted(edges,key=lambda i:(parse_time(predictions[i]['first_emitted_at']),predictions[i]['alert_id'])):augment(i,set())
    matched_predictions=set(owners.values());matches=[]
    for j,i in sorted(owners.items()):
        matches.append({'label_id':truth[j]['label_id'],'alert_id':predictions[i]['alert_id'],'asset_id':truth[j]['asset_id'],
            'type':truth[j]['type'],'delay_seconds':(parse_time(predictions[i]['first_emitted_at'])-parse_time(truth[j]['start'])).total_seconds()})
    tp=len(matches);fp=len(predictions)-tp;fn=len(truth)-tp
    normal_hours=sum((e-s).total_seconds()/3600 for rows in exposures.values() for s,e,n in rows if n)
    false_in_normal=sum(any(n and s<=parse_time(p['first_emitted_at'])<e for s,e,n in exposures[p['asset_id']]) for i,p in enumerate(predictions) if i not in matched_predictions)
    safe=lambda a,b:a/b if b else None
    p,r=safe(tp,tp+fp),safe(tp,tp+fn)
    delays=sorted(m['delay_seconds'] for m in matches)
    return {'scope':labels['scope'],'synthetic':labels['synthetic'],'evaluation_types':labels['evaluation_types'],
        'tp':tp,'fp':fp,'fn':fn,'precision':p,'recall':r,'f1':(2*p*r/(p+r) if p+r else 0.0) if p is not None and r is not None else None,
        'matched_detection_delay_median_seconds':__import__('statistics').median(delays) if delays else None,
        'normal_asset_hours':normal_hours,'false_first_alerts_during_normal_coverage':false_in_normal,
        'false_alerts_per_normal_asset_hour':safe(false_in_normal,normal_hours),'matches':matches,
        'unmatched_predictions':[p['alert_id'] for i,p in enumerate(predictions) if i not in matched_predictions],
        'missed_labels':[t['label_id'] for j,t in enumerate(truth) if j not in owners],
        'ignored_report_categories':sorted({a['type'] for a in result['first_alerts']}-set(labels['evaluation_types'])),
        'matching':f'maximum-cardinality one-to-one; identical asset and type; first alert in [label start, label end + {tolerance_seconds}s]; no early matching',
        'limitations':['Scores measure incident fusion on supplied adapter observations, not raw perception accuracy.',
            'Temporal cooccurrence is not causal diagnosis.','False alarms during fault coverage remain FP but are not counted in the normal-exposure rate.']}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--replay',type=Path,required=True);p.add_argument('--labels',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--tolerance-seconds',type=float,default=0);a=p.parse_args()
    if a.output.exists():p.error('Output file exists; choose a new filename')
    try:r=evaluate(json.loads(a.replay.read_text()),json.loads(a.labels.read_text()),a.tolerance_seconds)
    except (ValueError,OSError,KeyError) as e:p.error(str(e))
    write_json(a.output,r);print(json.dumps({k:r[k] for k in ('tp','fp','fn','precision','recall','f1')},indent=2))

if __name__=='__main__':main()
