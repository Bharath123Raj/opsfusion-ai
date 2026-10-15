"""Virtual chronological replay; first alerts freeze evidence available at emission."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import time
from .core import fuse
from .__main__ import load_events,write_json


def replay(events,window_seconds=90,engine="fixed",quiet_gap_seconds=20):
    if engine not in ('fixed','episode'):raise ValueError('Unknown replay engine')
    def fusion(items):
        if engine=='fixed':return fuse(items,window_seconds=window_seconds,standalone_audio_logs=True)
        from .segmentation import fuse_episodes
        return fuse_episodes(items,window_seconds=window_seconds,quiet_gap_seconds=quiet_gap_seconds)
    # Validate duplicates and config before producing any output.
    fusion(events)
    unique={e.event_id:e for e in events}
    ordered=sorted(unique.values(),key=lambda e:(e.time,e.event_id))
    canonical=[e.to_dict() for e in ordered]
    identity={'version':'replay_v1','events':canonical,'window_seconds':window_seconds}
    if engine=='episode':identity.update(version='episode_replay_v1',window_seconds=float(window_seconds),quiet_gap_seconds=float(quiet_gap_seconds))
    run_id='replay-'+hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:20]
    history=[];alerts={};updates=[];index=0;final=fusion([])
    # Equal-timestamp observations arrive together, preventing ID-order artifacts.
    while index<len(ordered):
        at=ordered[index].time;batch=[]
        while index<len(ordered) and ordered[index].time==at:
            batch.append(ordered[index]);index+=1
        history.extend(batch)
        current=fusion(history)
        known={e.event_id for e in history}
        for incident in current['incidents']:
            iid=incident['incident_id']
            assert set(incident['trigger_event_ids'])<=known
            if iid not in alerts:
                alerts[iid]={'alert_id':iid,'asset_id':incident['asset_id'],'type':incident['type'],
                    'first_emitted_at':at.isoformat(),'synthetic':incident['synthetic'],
                    'available_event_ids':sorted(known),'snapshot':copy.deepcopy(incident)}
            previous=next((i for i in final['incidents'] if i['incident_id']==iid),None)
            if previous!=incident:
                updates.append({'emitted_at':at.isoformat(),'incident_id':iid,'trigger_event_ids':incident['trigger_event_ids'],
                    'evidence_event_ids':[r['event_id'] for r in incident['timeline']],'risk':incident['risk']})
        final=current
    return {'run_id':run_id,'engine':'virtual_chronological_replay_v1' if engine=='fixed' else 'virtual_episode_replay_v1',
        'configuration':{'engine':engine,'window_seconds':window_seconds,'quiet_gap_seconds':quiet_gap_seconds if engine=='episode' else None},'input_events':len(unique),
        'first_alerts':list(alerts.values()),'updates':updates,'final_report':final,
        'assumptions':['Event timestamp is adapter availability time. Equal timestamps arrive together.',
            'Inputs are replayed in virtual time, not wall-clock real time.','No transport delay, out-of-order arrival or watermark handling.',
            'Snapshots freeze observation records, not external evidence-file bytes; batch episode files may include later context.',
            'This reference implementation retains history and re-fuses prefixes; not a production streaming engine.']}


def render_report(result):
    from html import escape
    from urllib.parse import urlsplit
    esc=lambda v:escape(str(v),quote=True)
    alerts=sorted(result['first_alerts'],key=lambda a:(a['first_emitted_at'],a['alert_id']))
    rows=[];details=[]
    for number,a in enumerate(alerts,1):
        snapshot=a['snapshot']
        rows.append(f"<tr><td>{esc(a['first_emitted_at'])}</td><td>{esc(a['asset_id'])}</td><td>{esc(a['type'].replace('_',' '))}</td><td>{esc(snapshot['risk'])}</td><td>{len(snapshot['trigger_event_ids'])}</td></tr>")
        facts=[]
        for fact in snapshot['observed_facts']:
            ref=fact['evidence_ref']
            reference=f'<a href="{esc(ref)}">Source evidence</a>' if urlsplit(ref).scheme in ('file','https','http') else esc(ref)
            facts.append(f"<li><strong>{esc(fact['event_id'])}</strong>: {esc(fact['summary'])}<br>{reference}</li>")
        details.append(f"<details><summary>{number}. {esc(a['type'].replace('_',' '))} · {esc(a['asset_id'])}</summary><p>First emission: {esc(a['first_emitted_at'])}</p><p><strong>Unconfirmed hypothesis:</strong> {esc(snapshot['hypothesis']['statement'])}</p><ul>{''.join(facts)}</ul><p>Recommendation: {esc(snapshot['recommended_action'])}</p></details>")
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>OpsFusion replay report</title>
<style>body{{font:16px/1.55 system-ui,sans-serif;background:#f3f6fb;color:#17283e;margin:0}}main{{max-width:1150px;margin:auto;padding:36px}}h1{{margin-bottom:4px}}.id{{overflow-wrap:anywhere;color:#526078}}.cards{{display:flex;gap:18px;margin:24px 0}}.card,details{{background:white;border:1px solid #dbe2ec;border-radius:10px;padding:16px;margin-bottom:12px}}.card{{flex:1}}.card b{{font-size:28px;display:block}}table{{border-collapse:collapse;width:100%;background:white;font-size:14px}}th,td{{text-align:left;border-bottom:1px solid #dbe2ec;padding:12px;overflow-wrap:anywhere}}th{{background:#e7eef8}}summary{{cursor:pointer;font-weight:600}}a{{color:#145aac}}li{{margin:12px 0}}.note{{border-left:4px solid #4773a8;padding:12px;background:white}}@media(max-width:700px){{main{{padding:16px}}.cards{{display:block}}table{{font-size:12px}}}}</style>
<main><h1>OpsFusion incident replay</h1><div class="id">{esc(result['run_id'])}</div><div class="cards"><div class="card"><b>{result['input_events']}</b>Observations</div><div class="card"><b>{len(alerts)}</b>First alerts across all categories</div><div class="card"><b>{len(result['updates'])}</b>Evidence updates</div></div>
<p class="note">Virtual replay freezes observation snapshots at first emission. It does not measure live service latency. Original adapter episode files may contain completed context after that timestamp. Causes remain unconfirmed.</p>
<h2>First-alert timeline</h2><table><thead><tr><th>Emission time (UTC)</th><th>Asset</th><th>Category</th><th>Rule priority</th><th>Triggers</th></tr></thead><tbody>{''.join(rows)}</tbody></table><h2>Evidence at first emission</h2>{''.join(details)}</main></html>'''


def save_replay(result,output,elapsed=None):
    output.mkdir(parents=True)
    write_json(output/'replay.json',result)
    (output/'report.html').write_text(render_report(result),encoding='utf-8')
    write_json(output/'incidents.json',result['final_report'])
    (output/'alerts.jsonl').write_text(''.join(json.dumps(a)+'\n' for a in result['first_alerts']),encoding='utf-8')
    (output/'updates.jsonl').write_text(''.join(json.dumps(u)+'\n' for u in result['updates']),encoding='utf-8')
    write_json(output/'run_manifest.json',{'status':'completed','run_id':result['run_id'],'input_events':result['input_events'],
        'first_alerts':len(result['first_alerts']),'updates':len(result['updates']),
        'configuration':result['configuration'],'wall_seconds':elapsed,'assumptions':result['assumptions']})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--events',type=Path,nargs='+',required=True)
    p.add_argument('--output',type=Path,default=Path('runs/replay-01'));p.add_argument('--window-seconds',type=float,default=90);p.add_argument('--engine',choices=['fixed','episode'],default='fixed');p.add_argument('--quiet-gap-seconds',type=float,default=20);a=p.parse_args()
    if a.output.exists():p.error('Output folder exists; choose a new --output')
    try:
        events=[e for path in a.events for e in load_events(path)]
        start=time.perf_counter();result=replay(events,a.window_seconds,a.engine,a.quiet_gap_seconds)
    except (ValueError,OSError) as e:p.error(str(e))
    save_replay(result,a.output,time.perf_counter()-start)
    print(f'COMPLETE REPLAY: {result["input_events"]} observations, {len(result["first_alerts"])} first alerts. Run: {result["run_id"]}. Outputs: {a.output}')

if __name__=='__main__':main()
