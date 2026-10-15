"""Local SQLite history. Operator identities are labels, not authenticated accounts."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from .core import Event, parse_time
from .segmentation import fuse_episodes
from . import correlation

ACTIVE = ('starting', 'running', 'reconnecting', 'stopping')
REVIEW_STATES = ('new', 'acknowledged', 'resolved', 'false_alarm')


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, allow_nan=False, sort_keys=True)


class History:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY, camera_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL, mode TEXT NOT NULL, state TEXT NOT NULL,
                    started_at TEXT NOT NULL, heartbeat TEXT NOT NULL, ended_at TEXT,
                    stop_requested INTEGER NOT NULL DEFAULT 0, error_code TEXT,
                    config TEXT NOT NULL, sampled_frames INTEGER NOT NULL DEFAULT 0,
                    event_count INTEGER NOT NULL DEFAULT 0, connection_epoch INTEGER NOT NULL DEFAULT 0,
                    active_anchor TEXT, latest_event_time TEXT, worker_pid INTEGER);
                CREATE UNIQUE INDEX IF NOT EXISTS one_camera_worker ON sessions(camera_id)
                    WHERE state IN ('starting','running','reconnecting','stopping');
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    timestamp TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS event_session_time ON events(session_id,timestamp);
                CREATE TABLE IF NOT EXISTS incidents (
                    incident_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    asset_id TEXT NOT NULL, kind TEXT NOT NULL, first_report TEXT NOT NULL,
                    current_report TEXT NOT NULL, first_saved_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    review_state TEXT NOT NULL DEFAULT 'new', revision INTEGER NOT NULL DEFAULT 1);
                CREATE INDEX IF NOT EXISTS incident_updated ON incidents(updated_at);
                CREATE TABLE IF NOT EXISTS audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT,
                    session_id TEXT NOT NULL, timestamp TEXT NOT NULL, actor TEXT NOT NULL,
                    action TEXT NOT NULL, note TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS notices (
                    notice_id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL, reason TEXT NOT NULL, dismissed INTEGER NOT NULL DEFAULT 0,
                    payload TEXT);
            ''')
            correlation.init_schema(db)
            if 'worker_pid' not in {r['name'] for r in db.execute('PRAGMA table_info(sessions)')}:
                db.execute('ALTER TABLE sessions ADD COLUMN worker_pid INTEGER')
            if 'payload' not in {r['name'] for r in db.execute('PRAGMA table_info(notices)')}:
                db.execute('ALTER TABLE notices ADD COLUMN payload TEXT')
                db.execute("UPDATE notices SET payload=(SELECT first_report FROM incidents WHERE incidents.incident_id=notices.incident_id) WHERE reason='first_alert'")
            if 'actor_origin' not in {r['name'] for r in db.execute('PRAGMA table_info(audit)')}:
                db.execute("ALTER TABLE audit ADD COLUMN actor_origin TEXT NOT NULL DEFAULT 'legacy_label'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=10000')
        db.execute('PRAGMA journal_mode=WAL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def start(self, camera_id, asset_id, mode, config, session_id=None):
        if mode not in ('webcam', 'rtsp', 'file_replay', 'import'):
            raise ValueError('Unsupported capture mode')
        if any(not isinstance(x, str) or not x.strip() or len(x)>200 for x in (camera_id,asset_id)):
            raise ValueError('Camera and asset IDs must contain 1–200 characters')
        # Never persist a source URL, credential, or arbitrary environment variable.
        allowed = {'polygon','confirm_samples','max_age_seconds','min_iou','sample_fps',
                   'confidence','quiet_gap_seconds','window_seconds','max_seconds','synthetic',
                   'max_events','max_episode_events','detector'}
        if set(config)-allowed:
            raise ValueError('Unsupported persisted capture setting')
        sid = session_id or uuid.uuid4().hex
        now = utc_now()
        state = 'completed' if mode=='import' else 'starting'
        try:
            with self.connect() as db:
                db.execute('INSERT INTO sessions(session_id,camera_id,asset_id,mode,state,started_at,heartbeat,config) VALUES(?,?,?,?,?,?,?,?)',
                           (sid,camera_id,asset_id,mode,state,now,now,encode(config)))
        except sqlite3.IntegrityError as exc:
            raise ValueError('Camera already has a worker, or this session was already imported') from exc
        return sid

    def sessions(self, limit=100):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM sessions ORDER BY state IN ('starting','running','reconnecting','stopping') DESC,started_at DESC LIMIT ?", (limit,))]

    def claim_worker(self, sid, pid):
        with self.connect() as db:
            result=db.execute("UPDATE sessions SET worker_pid=? WHERE session_id=? AND worker_pid IS NULL AND state='starting' AND stop_requested=0",(pid,sid))
            return result.rowcount==1

    def session(self, sid):
        with self.connect() as db:
            row = db.execute('SELECT * FROM sessions WHERE session_id=?',(sid,)).fetchone()
            if row is None:
                raise ValueError('Session not found')
            result = dict(row);result['config']=json.loads(result['config'])
            return result

    def heartbeat(self, sid, state='running', frames=None, error_code=None):
        if state not in ACTIVE:
            raise ValueError('Invalid worker state')
        with self.connect() as db:
            db.execute('UPDATE sessions SET state=?,heartbeat=?,sampled_frames=COALESCE(?,sampled_frames),error_code=? WHERE session_id=? AND state IN (\'starting\',\'running\',\'reconnecting\',\'stopping\')',
                       (state,utc_now(),frames,error_code,sid))

    def should_stop(self, sid):
        s=self.session(sid)
        return bool(s['stop_requested']) or s['state'] not in ACTIVE

    def request_stop(self, sid):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE sessions SET stop_requested=1,state=\'stopping\' WHERE session_id=? AND state IN (\'starting\',\'running\',\'reconnecting\')',(sid,))
            correlation.end_for_source(db,sid,utc_now())

    def finish(self, sid, state='stopped', error_code=None):
        if state not in ('stopped','completed','failed','interrupted'):
            raise ValueError('Invalid terminal state')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            correlation.end_for_source(db,sid,utc_now())
            db.execute('UPDATE sessions SET state=?,heartbeat=?,ended_at=?,error_code=? WHERE session_id=? AND state IN (\'starting\',\'running\',\'reconnecting\',\'stopping\')',
                       (state,utc_now(),utc_now(),error_code,sid))

    def recover_stale(self, sid):
        """Explicit recovery, never an automatic competing worker takeover."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            s=db.execute('SELECT * FROM sessions WHERE session_id=?',(sid,)).fetchone()
            if not s or s['state'] not in ACTIVE:
                raise ValueError('No active worker to recover')
            if (parse_time(utc_now())-parse_time(s['heartbeat'])).total_seconds()<120:
                raise ValueError('Worker heartbeat is recent; request Stop first')
            db.execute('UPDATE sessions SET stop_requested=1,state=\'interrupted\',ended_at=?,error_code=\'stale_worker_recovered\' WHERE session_id=?',(utc_now(),sid))
            correlation.end_for_source(db,sid,utc_now())

    def boundary(self, sid):
        """Break association and fusion continuity after missing footage."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            correlation.reset_for_source(db,sid,utc_now())
            db.execute('UPDATE sessions SET connection_epoch=connection_epoch+1,active_anchor=NULL WHERE session_id=?',(sid,))
            db.execute('INSERT INTO audit(session_id,timestamp,actor,action,note) VALUES(?,?,?,?,?)',
                       (sid,utc_now(),'system','capture_gap','Track identity and episode continuity reset; footage unavailable.'))

    def _save_incident(self, db, sid, report, first=None):
        report = dict(report)
        report['incident_id']=sid+':'+report['incident_id']
        iid=report['incident_id'];now=utc_now();payload=encode(report)
        existing=db.execute('SELECT * FROM incidents WHERE incident_id=?',(iid,)).fetchone()
        if existing is None:
            first=dict(first or report);first['incident_id']=iid
            db.execute('INSERT INTO incidents(incident_id,session_id,asset_id,kind,first_report,current_report,first_saved_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                       (iid,sid,report['asset_id'],report['type'],encode(first),payload,now,now))
            db.execute('INSERT INTO notices(incident_id,timestamp,reason,payload) VALUES(?,?,?,?)',(iid,now,'first_alert',encode(first)))
        elif existing['current_report']!=payload:
            reopened=existing['review_state'] in ('resolved','false_alarm')
            state='new' if reopened else existing['review_state']
            db.execute('UPDATE incidents SET current_report=?,updated_at=?,revision=revision+1,review_state=? WHERE incident_id=?',
                       (payload,now,state,iid))
            if reopened:
                db.execute('INSERT INTO notices(incident_id,timestamp,reason,payload) VALUES(?,?,?,?)',(iid,now,'new_evidence_after_review',payload))
                db.execute('INSERT INTO audit(incident_id,session_id,timestamp,actor,action,note) VALUES(?,?,?,?,?,?)',
                           (iid,sid,now,'system','reopened','New observations arrived after the operator closed this report.'))

    def ingest(self, sid, events):
        """Causal camera reports plus explicitly linked, bounded cross-source context."""
        events=sorted(events,key=lambda e:(e.time,e.event_id))
        if not events:
            return
        if any(e.modality!='video' or e.kind not in ('zone_entry','zone_presence') for e in events):
            raise ValueError('Live ingestion accepts camera zone observations only')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            s=db.execute('SELECT * FROM sessions WHERE session_id=?',(sid,)).fetchone()
            if not s or s['state'] not in ACTIVE or s['stop_requested']:
                raise ValueError('Worker no longer owns an active session')
            cfg=json.loads(s['config']);fresh=[]
            for e in events:
                if e.asset_id!=s['asset_id'] or e.source_id!=s['camera_id']:
                    raise ValueError('Event asset/camera does not match session')
                old=db.execute('SELECT session_id,payload FROM events WHERE event_id=?',(e.event_id,)).fetchone()
                if old:
                    if old['session_id']!=sid or old['payload']!=encode(e.to_dict()):
                        raise ValueError('Conflicting event identity')
                else:
                    fresh.append(e)
            if not fresh:
                return
            if s['latest_event_time'] and fresh[0].time<=parse_time(s['latest_event_time']):
                raise ValueError('Camera receipt clock moved backwards or repeated; equal-time observations must be one batch')
            if s['event_count']+len(fresh)>cfg.get('max_events',5000):
                raise ValueError('Session event capacity reached; stop and review saved history')
            active=[]
            if s['active_anchor']:
                active=[Event(**json.loads(r['payload'])) for r in db.execute('SELECT payload FROM events WHERE session_id=? AND timestamp>=? ORDER BY timestamp,event_id',(sid,s['active_anchor']))]
            combined=active+fresh
            gap=cfg.get('quiet_gap_seconds',20)
            groups=[]
            for e in combined:
                if not groups or (e.time-groups[-1][-1].time).total_seconds()>gap:
                    groups.append([])
                groups[-1].append(e)
            if any(len(g)>cfg.get('max_episode_events',200) for g in groups):
                raise ValueError('Episode event capacity reached; stop and review saved history')
            for e in fresh:
                db.execute('INSERT INTO events VALUES(?,?,?,?)',(e.event_id,sid,e.to_dict()['timestamp'],encode(e.to_dict())))
            report=fuse_episodes(combined,cfg.get('window_seconds',90),gap)
            for incident in report['incidents']:
                self._save_incident(db,sid,incident)
            db.execute('UPDATE sessions SET active_anchor=?,latest_event_time=?,event_count=event_count+? WHERE session_id=?',
                       (groups[-1][0].to_dict()['timestamp'],fresh[-1].to_dict()['timestamp'],len(fresh),sid))
            correlation.offer(self,db,sid,fresh,utc_now())

    def import_bundle(self, bundle):
        """Idempotent save of a completed dashboard run, preserving first snapshots."""
        import hashlib
        events=[Event(**e) for e in bundle['events']]
        sid='import-'+hashlib.sha256(encode({'events':bundle['events'],'settings':bundle['review_settings']}).encode()).hexdigest()[:24]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM sessions WHERE session_id=?',(sid,)).fetchone():
                return sid
            now=utc_now()
            db.execute('INSERT INTO sessions(session_id,camera_id,asset_id,mode,state,started_at,heartbeat,ended_at,config,event_count) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (sid,'recorded-import','multiple-assets','import','completed',now,now,now,'{}',len(events)))
            # Imported runs may intentionally repeat canonical events from another run.
            # Their report snapshots retain canonical IDs; the event table scopes the storage ID.
            for e in events:
                db.execute('INSERT INTO events VALUES(?,?,?,?)',(sid+':'+e.event_id,sid,e.to_dict()['timestamp'],encode(e.to_dict())))
            first={a['snapshot']['incident_id']:a['snapshot'] for a in bundle['replay']['first_alerts']}
            for i in bundle['replay']['final_report']['incidents']:
                self._save_incident(db,sid,i,first.get(i['incident_id']))
        return sid

    def incidents(self, state='all', limit=100):
        if state not in ('all',*REVIEW_STATES):
            raise ValueError('Invalid review filter')
        with self.connect() as db:
            rows=db.execute('SELECT * FROM incidents WHERE (?=\'all\' OR review_state=?) ORDER BY updated_at DESC,incident_id LIMIT ?', (state,state,limit))
            result=[]
            for row in rows:
                d=dict(row)
                for k in ('first_report','current_report'):
                    d[k]=json.loads(d[k])
                result.append(d)
            return result

    def review(self, iid, state, actor, note, expected_revision, authenticated=False):
        if state not in REVIEW_STATES or not isinstance(actor,str) or not actor.strip() or len(actor)>100 or not isinstance(note,str) or len(note)>2000:
            raise ValueError('Choose a valid state, operator label (1–100 characters), and note (up to 2000 characters)')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            r=db.execute('SELECT * FROM incidents WHERE incident_id=?',(iid,)).fetchone()
            if not r:
                raise ValueError('Incident not found')
            if r['revision']!=expected_revision:
                raise ValueError('Report changed since selection. Refresh and review the latest evidence.')
            db.execute('UPDATE incidents SET review_state=?,revision=revision+1,updated_at=? WHERE incident_id=?',(state,utc_now(),iid))
            db.execute('INSERT INTO audit(incident_id,session_id,timestamp,actor,action,note,actor_origin) VALUES(?,?,?,?,?,?,?)',
                       (iid,r['session_id'],utc_now(),actor.strip(),state,note,'authenticated_account' if authenticated else 'legacy_label'))

    def audit(self, iid):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM audit WHERE incident_id=? ORDER BY audit_id',(iid,))]

    def notices(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM notices WHERE dismissed=0 ORDER BY notice_id DESC LIMIT 100')]

    def dismiss_notice(self, notice_id):
        with self.connect() as db:
            db.execute('UPDATE notices SET dismissed=1 WHERE notice_id=?',(notice_id,))

    def export(self):
        with self.connect() as db:
            db.execute('BEGIN')
            incidents=[]
            for row in db.execute('SELECT * FROM incidents ORDER BY updated_at DESC,incident_id'):
                value=dict(row)
                for k in ('first_report','current_report'):
                    value[k]=json.loads(value[k])
                incidents.append(value)
            return {'schema_version':'history-1.0','exported_at':utc_now(),
                    'sessions':[dict(r) for r in db.execute('SELECT * FROM sessions ORDER BY started_at')],
                    'incidents':incidents,
                    'audit':[dict(r) for r in db.execute('SELECT * FROM audit ORDER BY audit_id')],
                    'notices':[dict(r) for r in db.execute('SELECT * FROM notices ORDER BY notice_id')],
                    'limitations':['Legacy operator labels remain unverified; actor_origin identifies authenticated dashboard reviews.',
                                   'Review decisions do not alter detector facts or verify authorization.',
                                   'Evidence files stay on this computer; JSON is not a media backup.']}
