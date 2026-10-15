"""Transactional, chronological sensor/controller ingestion into shared local history."""
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
import uuid

from .accounts import Accounts
from . import correlation
from .core import Event, parse_time
from .history import History, encode
from .logs import WARN_CODES, STOP_CODES
from .segmentation import fuse_episodes
from .sensor import FEATURES, fit_baselines, read_csv, score_rows, libs, digest

MAX_INPUTS=10000
MAX_EVENTS=200
ID_PATTERN=r'[A-Za-z0-9_.-]{1,120}'


class StreamConflict(ValueError):pass


class StreamStore:
    def __init__(self,root,accounts_path=None,clock=time.time):
        self.root=Path(root).resolve();self.clock=clock
        self.accounts=Accounts(accounts_path or self.root/'runs/accounts.sqlite3')
        self.history=History(self.root/'runs/history.sqlite3')
        self._cache=OrderedDict();self._lock=threading.Lock()
        with self.history.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS stream_baselines (
                    baseline_id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL,created_by TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS live_streams (
                    stream_id TEXT PRIMARY KEY REFERENCES sessions(session_id),baseline_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,source_id TEXT NOT NULL,source_synthetic INTEGER NOT NULL,
                    state TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL,closed_at TEXT,
                    last_timestamp TEXT,last_sensor_time TEXT,last_log_time TEXT,last_received_at TEXT,
                    input_count INTEGER NOT NULL DEFAULT 0,reading_count INTEGER NOT NULL DEFAULT 0,
                    log_count INTEGER NOT NULL DEFAULT 0,event_count INTEGER NOT NULL DEFAULT 0,
                    gap_count INTEGER NOT NULL DEFAULT 0,detector_state TEXT NOT NULL DEFAULT '{}');
                CREATE UNIQUE INDEX IF NOT EXISTS one_open_stream_asset ON live_streams(asset_id) WHERE state='open';
                CREATE TABLE IF NOT EXISTS stream_inputs (
                    stream_id TEXT NOT NULL REFERENCES live_streams(stream_id),message_id TEXT NOT NULL,
                    kind TEXT NOT NULL,timestamp TEXT NOT NULL,received_at TEXT NOT NULL,
                    payload TEXT NOT NULL,result TEXT NOT NULL,actor TEXT NOT NULL,
                    PRIMARY KEY(stream_id,message_id));
                CREATE INDEX IF NOT EXISTS stream_input_time ON stream_inputs(stream_id,timestamp);
            ''')

    def now(self):return datetime.fromtimestamp(self.clock(),timezone.utc).isoformat()

    def register(self,token,train_path,validation_path):
        actor=self.accounts.require(token,'model_admin')['username']
        train=read_csv(Path(train_path));validation=read_csv(Path(validation_path))
        bundle,metadata=fit_baselines(train,validation)
        identifier=uuid.uuid4().hex
        folder=self.root/'runs/stream-baselines'/identifier;folder.mkdir(parents=True)
        path=folder/'sensor.joblib';_,_,joblib,_=libs();joblib.dump(bundle,path)
        fingerprint=digest(path)
        with self.history.connect() as db:
            db.execute('INSERT INTO stream_baselines VALUES(?,?,?,?,?)',(identifier,fingerprint,encode(metadata),self.now(),actor))
        return {'baseline_id':identifier,'sha256':fingerprint,'assets':metadata}

    def baselines(self,token):
        self.accounts.require(token,'read')
        with self.history.connect() as db:
            return [{**dict(r),'metadata':json.loads(r['metadata'])} for r in db.execute('SELECT * FROM stream_baselines ORDER BY created_at DESC')]

    def _bundle(self,baseline_id):
        if not re.fullmatch('[0-9a-f]{32}',baseline_id):raise ValueError('Use a registered baseline ID')
        with self.history.connect() as db:record=db.execute('SELECT * FROM stream_baselines WHERE baseline_id=?',(baseline_id,)).fetchone()
        if record is None:raise ValueError('Baseline not registered; prepare a trusted normal baseline locally')
        with self._lock:
            if baseline_id in self._cache:
                self._cache.move_to_end(baseline_id);return self._cache[baseline_id]
            path=self.root/'runs/stream-baselines'/baseline_id/'sensor.joblib'
            try:
                path.resolve().relative_to((self.root/'runs/stream-baselines').resolve())
                if digest(path)!=record['sha256']:raise ValueError('Registered model changed; register a new baseline')
                _,_,joblib,_=libs();bundle=joblib.load(path)
                # Validate the same recorded format/version used by batch scoring.
                if bundle.get('format')!='opsfusion_sensor_v1' or bundle.get('features')!=list(FEATURES):raise ValueError('Incompatible registered model')
                np,sklearn,_,_=libs()
                if bundle['sklearn_version']!=sklearn.__version__:raise ValueError('Model sklearn version differs; retrain')
            except OSError:raise ValueError('Registered model unavailable; restore its installation folder') from None
            self._cache[baseline_id]=bundle
            if len(self._cache)>16:self._cache.popitem(last=False)
            return bundle

    def open(self,token,baseline_id,asset_id,source_id,synthetic):
        actor=self.accounts.require(token,'analyse')['username']
        if any(not isinstance(x,str) or not re.fullmatch(ID_PATTERN,x) for x in (asset_id,source_id)) or type(synthetic)!=bool:
            raise ValueError('Asset/source IDs must use 1–120 letters, digits, dots, underscores or hyphens; declare synthetic')
        bundle=self._bundle(baseline_id)
        if asset_id not in bundle['models']:raise ValueError('Asset is not covered by this baseline')
        sid=uuid.uuid4().hex;tick=self.now()
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM live_streams WHERE asset_id=? AND state='open'",(asset_id,)).fetchone():
                raise StreamConflict('An open HTTP stream already owns this asset; close it before starting another')
            if db.execute("SELECT COUNT(*) FROM live_streams WHERE state='open'").fetchone()[0]>=20:
                raise StreamConflict('Maximum 20 open HTTP streams; close unused streams')
            config={'detector':'http_sensor_controller_v1','synthetic':bool(synthetic or bundle['models'][asset_id]['trained_synthetic']),
                    'quiet_gap_seconds':20,'window_seconds':90,'max_events':MAX_EVENTS}
            db.execute("INSERT INTO sessions(session_id,camera_id,asset_id,mode,state,started_at,heartbeat,config) VALUES(?,?,?,'http_stream','running',?,?,?)",
                       (sid,'http:'+sid,asset_id,tick,tick,encode(config)))
            db.execute('INSERT INTO live_streams(stream_id,baseline_id,asset_id,source_id,source_synthetic,state,created_by,created_at) VALUES(?,?,?,?,?,\'open\',?,?)',
                       (sid,baseline_id,asset_id,source_id,int(synthetic),actor,tick))
            db.execute('INSERT INTO audit(session_id,timestamp,actor,action,note,actor_origin) VALUES(?,?,?,?,?,?)',
                       (sid,tick,actor,'http_stream_opened','Registered baseline; measurement units and clocks require upstream verification.','authenticated_account'))
        return self.get(token,sid)

    def get(self,token,sid):
        self.accounts.require(token,'read')
        with self.history.connect() as db:
            row=db.execute('SELECT * FROM live_streams WHERE stream_id=?',(sid,)).fetchone()
            if row is None:raise ValueError('Stream not found')
            return self._describe(db,row)

    def _describe(self,db,row):
        result=dict(row);result.pop('detector_state')
        result['source_synthetic']=bool(result['source_synthetic'])
        metadata=json.loads(db.execute('SELECT metadata FROM stream_baselines WHERE baseline_id=?',(result['baseline_id'],)).fetchone()[0])
        result['effective_synthetic']=bool(result['source_synthetic'] or metadata[result['asset_id']]['trained_synthetic'])
        result['last_receipt_age_seconds']=None if result['last_received_at'] is None else max(0,self.clock()-parse_time(result['last_received_at']).timestamp())
        result['input_status']='closed' if result['state']!='open' else 'waiting' if result['last_received_at'] is None else 'stale' if result['last_receipt_age_seconds']>10 else 'receiving'
        return result

    def streams(self,token):
        self.accounts.require(token,'read')
        with self.history.connect() as db:ids=[r[0] for r in db.execute('SELECT stream_id FROM live_streams ORDER BY created_at DESC LIMIT 100')]
        return [self.get(token,sid) for sid in ids]

    def close(self,token,sid):
        actor=self.accounts.require(token,'analyse')['username'];tick=self.now()
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM live_streams WHERE stream_id=?',(sid,)).fetchone()
            if row is None:raise ValueError('Stream not found')
            if row['state']=='open':
                correlation.end_for_source(db,sid,tick)
                db.execute("UPDATE live_streams SET state='closed',closed_at=? WHERE stream_id=?",(tick,sid))
                db.execute("UPDATE sessions SET state='completed',ended_at=?,heartbeat=? WHERE session_id=?",(tick,tick,sid))
                db.execute('INSERT INTO audit(session_id,timestamp,actor,action,note,actor_origin) VALUES(?,?,?,?,?,?)',
                           (sid,tick,actor,'http_stream_closed','Closing an input session does not confirm machine recovery.','authenticated_account'))
        return self.get(token,sid)

    def records(self,token,sid,limit=100):
        self.get(token,sid)
        with self.history.connect() as db:
            return [{**dict(r),'payload':json.loads(r['payload']),'result':json.loads(r['result'])} for r in db.execute(
                'SELECT * FROM stream_inputs WHERE stream_id=? ORDER BY received_at DESC,rowid DESC LIMIT ?',(sid,limit))]

    def export(self,token,sid):
        self.accounts.require(token,'read')
        with self.history.connect() as db:
            db.execute('BEGIN')
            row=db.execute('SELECT * FROM live_streams WHERE stream_id=?',(sid,)).fetchone()
            if row is None:raise ValueError('Stream not found')
            stream=self._describe(db,row)
            baseline=db.execute('SELECT baseline_id,sha256,metadata FROM stream_baselines WHERE baseline_id=?',(stream['baseline_id'],)).fetchone()
            provenance={'baseline_id':baseline['baseline_id'],'sha256':baseline['sha256'],
                        'asset_metadata':json.loads(baseline['metadata'])[stream['asset_id']],
                        'model_format':'opsfusion_sensor_v1'}
            rows=[{**dict(r),'payload':json.loads(r['payload']),'result':json.loads(r['result'])} for r in db.execute('SELECT * FROM stream_inputs WHERE stream_id=? ORDER BY rowid',(sid,))]
            events=[json.loads(r[0]) for r in db.execute('SELECT payload FROM events WHERE session_id=? ORDER BY timestamp,event_id',(sid,))]
        return {'schema_version':'http-stream-1.0','stream':stream,'baseline':provenance,'inputs':rows,'events':events,
                'limits':{'max_inputs':MAX_INPUTS,'max_events':MAX_EVENTS},
                'limitations':['Raw timestamps are client acquisition declarations; clock accuracy is unverified.',
                               'Arrival order is enforced; stale/future/out-of-order inputs are rejected.',
                               'No hardware or real-world accuracy is established by the test stream.']}

    def accept(self,token,sid,kind,payload):
        actor=self.accounts.require(token,'analyse')['username']
        if kind not in ('sensor','log'):raise ValueError('Use sensor or log input')
        required={'message_id','timestamp','asset_id','synthetic'} | (set(FEATURES) if kind=='sensor' else {'level','code','message'})
        if not isinstance(payload,dict) or set(payload)!=required:raise ValueError('Input fields do not match the declared measurement/log contract')
        if not isinstance(payload['message_id'],str) or not re.fullmatch(ID_PATTERN,payload['message_id']):raise ValueError('Invalid message ID')
        if type(payload['synthetic'])!=bool:raise ValueError('Declare synthetic as a boolean')
        try:dt=parse_time(payload['timestamp'])
        except (ValueError,TypeError,AttributeError):raise ValueError('Timestamp must be timezone-aware ISO 8601') from None
        canonical={**payload,'timestamp':dt.isoformat()}
        if kind=='sensor':
            if any(type(payload[f]) not in (float,int) or not math.isfinite(payload[f]) for f in FEATURES) or payload[FEATURES[0]]<0:
                raise ValueError('Measurements must be finite; RMS vibration is nonnegative and uses mm/s')
        else:
            if payload['level'] not in ('DEBUG','INFO','WARN','ERROR','CRITICAL') or not isinstance(payload['code'],str) or not re.fullmatch('[A-Z0-9_]{1,64}',payload['code']) or not isinstance(payload['message'],str) or not payload['message'].strip() or len(payload['message'])>1000:
                raise ValueError('Invalid controller level, code or message')
        with self.history.connect() as db:
            s=db.execute('SELECT * FROM live_streams WHERE stream_id=?',(sid,)).fetchone()
        if s is None:raise ValueError('Stream not found')
        bundle=self._bundle(s['baseline_id']);model=bundle['models'][s['asset_id']]
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            s=db.execute('SELECT * FROM live_streams WHERE stream_id=?',(sid,)).fetchone()
            old=db.execute('SELECT * FROM stream_inputs WHERE stream_id=? AND message_id=?',(sid,payload['message_id'])).fetchone()
            if old:
                if old['kind']!=kind or old['payload']!=encode(canonical):raise StreamConflict('Message ID already has different content')
                return {**json.loads(old['result']),'replayed':True}
            if s['state']!='open':raise StreamConflict('Stream is closed; previously accepted retries remain readable')
            if s['input_count']>=MAX_INPUTS:raise StreamConflict('Stream input capacity reached; close and review the saved stream')
            if payload['asset_id']!=s['asset_id'] or payload['synthetic']!=bool(s['source_synthetic']):raise ValueError('Input asset/synthetic declaration does not match its stream')
            age=self.clock()-dt.timestamp()
            if age>30 or age < -5:raise ValueError('Timestamp outside the live receipt window: at most 30 seconds old or 5 seconds future')
            if s['last_timestamp'] and dt<parse_time(s['last_timestamp']):raise StreamConflict('Out-of-order input; late arrivals are not rewritten')
            last_kind=s['last_sensor_time'] if kind=='sensor' else s['last_log_time']
            if last_kind and dt<=parse_time(last_kind):raise StreamConflict('Timestamps must increase within each input kind')
            tick=self.now();state=json.loads(s['detector_state']);event=None;gap=False;scored=None
            synthetic=bool(payload['synthetic'] or model['trained_synthetic'])
            if kind=='sensor':
                scored=score_rows(bundle,[{'timestamp':dt.isoformat(),'asset_id':s['asset_id'],
                                          'values':[payload[f] for f in FEATURES],'synthetic':payload['synthetic'],'csv_line':None}])[0][0]
                gap=last_kind is not None and (dt-parse_time(last_kind)).total_seconds()>model['cadence_seconds']*2
                if gap:
                    correlation.reset_for_source(db,sid,tick)
                    state={}
                    db.execute('UPDATE sessions SET connection_epoch=connection_epoch+1,active_anchor=NULL WHERE session_id=?',(sid,))
                    db.execute('INSERT INTO audit(session_id,timestamp,actor,action,note,actor_origin) VALUES(?,?,?,?,?,?)',
                               (sid,tick,'system','sensor_gap','Persistence and event fusion continuity reset; no recovery inferred.','system'))
                if not scored['candidate']:state={}
                else:
                    state['streak']=state.get('streak',0)+1
                    state['confirmation_ids']=(state.get('confirmation_ids',[])+[payload['message_id']])[-3:]
                confirmed=bool(scored['candidate'] and state.get('streak',0)>=3)
                scored['alert']=confirmed
                if confirmed and not state.get('active',False):
                    state['active']=True
                    eid='http-'+sid+'-sensor-'+hashlib.sha256(payload['message_id'].encode()).hexdigest()[:20]
                    event=Event(eid,dt.isoformat(),s['asset_id'],'sensor','sensor_anomaly',s['source_id'],
                                f'opsfusion://stream/{sid}/input/{payload["message_id"]}',
                                f'Persistent sensor deviation confirmed from 3 ordered readings; vibration RMS {payload[FEATURES[0]]:.3f} mm/s, temperature {payload[FEATURES[1]]:.3f} C; cause unconfirmed.',1.,synthetic)
            else:
                log_kind='motor_warning' if payload['code'] in WARN_CODES and payload['level'] in ('WARN','ERROR','CRITICAL') else 'shutdown' if payload['code'] in STOP_CODES else None
                if log_kind:
                    eid='http-'+sid+'-log-'+hashlib.sha256(payload['message_id'].encode()).hexdigest()[:20]
                    event=Event(eid,dt.isoformat(),s['asset_id'],'log',log_kind,s['source_id']+'-controller',
                                f'opsfusion://stream/{sid}/input/{payload["message_id"]}',
                                f'Controller reported {payload["code"]} ({payload["level"]}): {payload["message"]}. Reported state, not verified diagnosis.',1.,synthetic)
            if event:
                if s['event_count']>=MAX_EVENTS:raise StreamConflict('Stream event capacity reached; close and review saved evidence')
                session=db.execute('SELECT * FROM sessions WHERE session_id=?',(sid,)).fetchone()
                active=[]
                if session['active_anchor']:
                    active=[Event(**json.loads(r[0])) for r in db.execute('SELECT payload FROM events WHERE session_id=? AND timestamp>=? ORDER BY timestamp,event_id',(sid,session['active_anchor']))]
                combined=active+[event]
                report=fuse_episodes(combined,90,20)
                for incident in report['incidents']:self.history._save_incident(db,sid,incident)
                db.execute('INSERT INTO events VALUES(?,?,?,?)',(event.event_id,sid,event.timestamp,encode(event.to_dict())))
                latest=report['episodes'][-1]
                db.execute('UPDATE sessions SET active_anchor=?,latest_event_time=?,event_count=event_count+1 WHERE session_id=?',
                           (latest['start'],event.timestamp,sid))
            result={'accepted':True,'replayed':False,'message_id':payload['message_id'],'received_at':tick,
                    'stream_id':sid,'input_kind':kind,'synthetic':synthetic,'gap_reset':gap,
                    'score':None if scored is None else {k:scored[k] for k in ('if_score','if_threshold','robust_z','candidate','alert')},
                    'event_id':None if event is None else event.event_id}
            if kind=='sensor':result['confirmation_input_ids']=state.get('confirmation_ids',[]) if event else []
            db.execute('INSERT INTO stream_inputs VALUES(?,?,?,?,?,?,?,?)',
                       (sid,payload['message_id'],kind,dt.isoformat(),tick,encode(canonical),encode(result),actor))
            db.execute('UPDATE live_streams SET last_timestamp=?,last_sensor_time=?,last_log_time=?,last_received_at=?,input_count=input_count+1,reading_count=reading_count+?,log_count=log_count+?,event_count=event_count+?,gap_count=gap_count+?,detector_state=? WHERE stream_id=?',
                       (dt.isoformat(),dt.isoformat() if kind=='sensor' else s['last_sensor_time'],dt.isoformat() if kind=='log' else s['last_log_time'],tick,int(kind=='sensor'),int(kind=='log'),int(event is not None),int(gap),encode(state),sid))
            db.execute('UPDATE sessions SET heartbeat=?,sampled_frames=sampled_frames+? WHERE session_id=?',(tick,int(kind=='sensor'),sid))
            if event:correlation.offer(self.history,db,sid,[event],tick)
        return result
