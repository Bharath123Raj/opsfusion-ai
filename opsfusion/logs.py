"""Explicit structured-code mapping; no free-text cause inference."""
import argparse,csv,json
from pathlib import Path
from .core import Event,fuse,parse_time
from .__main__ import write_json

FIELDS={'timestamp','asset_id','source_id','level','code','message','synthetic'}
WARN_CODES={'TEMP_WARNING','VIBRATION_WARNING','MOTOR_OVERLOAD'}
STOP_CODES={'UNEXPECTED_SHUTDOWN','PROTECTIVE_SHUTDOWN'}


def parse_logs(path):
    events=[];unmapped=[];seen=set();count=0
    with path.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames is None or set(reader.fieldnames)!=FIELDS or len(reader.fieldnames)!=len(FIELDS):raise ValueError('Log CSV has wrong columns')
        for line,row in enumerate(reader,2):
            count+=1
            if any(not isinstance(v,str) or not v.strip() for v in row.values()):raise ValueError(f'Log line {line}: missing value')
            timestamp=parse_time(row['timestamp']).isoformat();synthetic=row['synthetic'].lower()
            if synthetic not in ('true','false'):raise ValueError('Invalid log synthetic flag')
            level=row['level'].upper();code=row['code'].upper()
            if level not in ('DEBUG','INFO','WARN','ERROR','CRITICAL'):raise ValueError('Invalid log level')
            key=(timestamp,row['asset_id'],row['source_id'],code,row['message'],level,synthetic)
            if key in seen:continue
            seen.add(key)
            kind='motor_warning' if code in WARN_CODES and level in ('WARN','ERROR','CRITICAL') else ('shutdown' if code in STOP_CODES else None)
            if kind is None:unmapped.append({'csv_line':line,'code':code,'level':level});continue
            import hashlib
            token=hashlib.sha256(json.dumps(key).encode()).hexdigest()[:20]
            events.append(Event('log-'+token,timestamp,row['asset_id'],'log',kind,row['source_id'],path.resolve().as_uri()+f'#csv_line={line}',
                f'Controller reported {code} ({level}): {row["message"]}. Reported state, not independently verified diagnosis.',1.,synthetic=='true'))
    return events,{'input_rows':count,'mapped_events':len(events),'unmapped_records':unmapped}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--csv',type=Path,required=True);p.add_argument('--output',type=Path,default=Path('runs/logs-01'));args=p.parse_args()
    if args.output.exists():p.error('Output folder exists; choose a new --output')
    try:events,metadata=parse_logs(args.csv)
    except (ValueError,OSError) as e:p.error(str(e))
    args.output.mkdir(parents=True)
    (args.output/'log_events.jsonl').write_text(''.join(json.dumps(e.to_dict())+'\n' for e in events),encoding='utf-8')
    write_json(args.output/'incidents.json',fuse(events,standalone_audio_logs=True));write_json(args.output/'run_manifest.json',{'status':'completed',**metadata})
    print(f'COMPLETE LOGS: {metadata["input_rows"]} records, {len(events)} mapped events. Outputs: {args.output}')

if __name__=='__main__':main()
