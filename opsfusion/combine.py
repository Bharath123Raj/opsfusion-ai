"""Combine already-produced adapter observations, preserving their provenance."""
import argparse,json
from pathlib import Path
from .__main__ import load_events,write_json
from .core import fuse

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--events',type=Path,nargs='+',required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if args.output.exists():p.error('Output folder exists; choose another output')
    try:
        events=[e for path in args.events for e in load_events(path)];result=fuse(events,standalone_audio_logs=True)
    except (ValueError,OSError) as e:p.error(str(e))
    args.output.mkdir(parents=True)
    # Store the deduplicated observations, including any low-score input evidence.
    unique={e.event_id:e for e in events}
    (args.output/'combined_events.jsonl').write_text(''.join(json.dumps(e.to_dict())+'\n' for e in sorted(unique.values(),key=lambda e:(e.time,e.event_id))),encoding='utf-8')
    write_json(args.output/'incidents.json',result)
    write_json(args.output/'run_manifest.json',{'status':'completed','input_files':len(args.events),'unique_observations':len(unique),
        'incidents':len(result['incidents']),'synthetic_observations':sum(e.synthetic for e in unique.values()),
        'note':'Cooccurrence is not causality; mixed real and synthetic sources are composed replay.'})
    print(f'COMPLETE FUSION: {len(unique)} observations, {len(result["incidents"])} incidents. Outputs: {args.output}')

if __name__=='__main__':main()
