"""Opt-in camera/HTTP cooccurrence. Called inside the source write transaction."""
from datetime import timedelta
import json
import math
import uuid

from .core import Event, build_incident, parse_time

MAX_WATCH_EVENTS = 1000
MACHINE_KINDS = {'sensor_anomaly', 'motor_warning', 'shutdown'}
CAMERA_KINDS = {'zone_entry', 'zone_presence'}
LIMITATIONS = [
    'A joint report describes temporal cooccurrence, not causation or verified danger.',
    'Camera time is host frame receipt time; HTTP acquisition times are client declarations.',
    'Only new observations after linking are eligible. No historical backfill.',
    'A camera observation anchors a fixed window; warning chains cannot extend it.',
    'Sparse observations do not establish continuous presence, ongoing faults or recovery.',
    'Evidence JSON contains references and stored measurements, not a media backup.',
]


def encode(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def init_schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS watch_site (singleton INTEGER PRIMARY KEY CHECK(singleton=1),installation_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS asset_watches (
            watch_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
            camera_session TEXT NOT NULL REFERENCES sessions(session_id),
            http_stream TEXT NOT NULL REFERENCES sessions(session_id),
            asset_id TEXT NOT NULL, horizon_seconds REAL NOT NULL,
            state TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
            closed_at TEXT, close_reason TEXT, generation INTEGER NOT NULL DEFAULT 0,
            generation_started_at TEXT NOT NULL, event_count INTEGER NOT NULL DEFAULT 0,
            excluded_count INTEGER NOT NULL DEFAULT 0, reset_count INTEGER NOT NULL DEFAULT 0);
        CREATE UNIQUE INDEX IF NOT EXISTS watch_camera ON asset_watches(camera_session) WHERE state='active';
        CREATE UNIQUE INDEX IF NOT EXISTS watch_http ON asset_watches(http_stream) WHERE state='active';
        CREATE TABLE IF NOT EXISTS watch_events (
            watch_id TEXT NOT NULL REFERENCES asset_watches(watch_id),
            event_id TEXT NOT NULL, source_session TEXT NOT NULL,
            generation INTEGER NOT NULL, timestamp TEXT NOT NULL,
            received_at TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(watch_id,event_id));
        CREATE INDEX IF NOT EXISTS watch_event_time ON watch_events(watch_id,generation,timestamp);
    ''')
    db.execute('INSERT OR IGNORE INTO watch_site VALUES(1,?)',(uuid.uuid4().hex,))


def _audit(db, wid, tick, actor, action, note, origin='system'):
    db.execute('INSERT INTO audit(session_id,timestamp,actor,action,note,actor_origin) VALUES(?,?,?,?,?,?)',
               (wid, tick, actor, action, note, origin))


def _end(db, watch, tick, reason, actor='system', origin='system'):
    db.execute("UPDATE asset_watches SET state='closed',closed_at=?,close_reason=? WHERE watch_id=? AND state='active'",
               (tick, reason, watch['watch_id']))
    db.execute("UPDATE sessions SET state='completed',heartbeat=?,ended_at=? WHERE session_id=?", (tick, tick, watch['watch_id']))
    _audit(db, watch['watch_id'], tick, actor, 'asset_watch_closed', reason, origin)


def end_for_source(db, sid, tick):
    for w in db.execute("SELECT * FROM asset_watches WHERE state='active' AND (camera_session=? OR http_stream=?)", (sid, sid)).fetchall():
        _end(db, w, tick, 'linked_source_closed')


def reset_for_source(db, sid, tick):
    for w in db.execute("SELECT * FROM asset_watches WHERE state='active' AND (camera_session=? OR http_stream=?)", (sid, sid)).fetchall():
        db.execute('UPDATE asset_watches SET generation=generation+1,generation_started_at=?,reset_count=reset_count+1 WHERE watch_id=?', (tick, w['watch_id']))
        _audit(db, w['watch_id'], tick, 'system', 'asset_watch_gap', 'Cross-source continuity reset; earlier observations cannot pair across this gap.')


def _report(w, video, signals, receipt):
    evidence = sorted([video, *signals], key=lambda e: (e.time, e.event_id))
    r = build_incident(w['asset_id'], 'restricted_zone' if video.kind == 'zone_entry' else 'restricted_zone_presence', evidence, evidence)
    r.update(incident_id='cooccurrence:'+video.event_id, type='zone_machine_cooccurrence', risk='MEDIUM',
             detected_at=receipt, segmentation='camera_anchor_v1',
             hypothesis={'statement': 'Zone activity and machine signals observed close in time',
                         'status': 'unconfirmed',
                         'alternatives': ['Coincidental activity', 'Authorized maintenance', 'Clock or measurement error']},
             recommended_action='Review the camera snapshot, access authorization, measurements and controller state together. Do not infer causation or restart equipment from this report.',
             correlation={'watch_id': w['watch_id'], 'camera_anchor_event': video.event_id,
                          'camera_observation_kind': video.kind, 'horizon_seconds': w['horizon_seconds'],
                          'generation': w['generation'], 'relationship': 'temporal_cooccurrence_only'})
    return r


def offer(history, db, sid, events, receipt):
    """Atomic with ingestion. Cross-source arrival order may differ by <=30s.

    A first report contains only committed arrivals available at that moment.
    Later evidence revises current_report; its first detected_at stays frozen.
    """
    watches = db.execute("SELECT * FROM asset_watches WHERE state='active' AND (camera_session=? OR http_stream=?)", (sid, sid)).fetchall()
    for initial in watches:
        for event in events:
            w = db.execute('SELECT * FROM asset_watches WHERE watch_id=?', (initial['watch_id'],)).fetchone()
            if w['state'] != 'active': break
            expected = (event.modality == 'video' and event.kind in CAMERA_KINDS) if sid == w['camera_session'] else (event.modality in ('sensor', 'log') and event.kind in MACHINE_KINDS)
            if not expected or event.confidence < .6 or event.asset_id != w['asset_id']: continue
            if db.execute('SELECT 1 FROM watch_events WHERE watch_id=? AND event_id=?', (w['watch_id'], event.event_id)).fetchone(): continue
            age = (parse_time(receipt)-event.time).total_seconds()
            if age > 30 or age < -5 or event.time < parse_time(w['generation_started_at']):
                db.execute('UPDATE asset_watches SET excluded_count=excluded_count+1 WHERE watch_id=?', (w['watch_id'],))
                continue
            if w['event_count'] >= MAX_WATCH_EVENTS:
                _end(db, w, receipt, 'watch_capacity_reached')
                break  # Source ingestion keeps working; review and create a new watch.
            db.execute('INSERT INTO watch_events VALUES(?,?,?,?,?,?,?)', (w['watch_id'], event.event_id, sid, w['generation'], event.to_dict()['timestamp'], receipt, encode(event.to_dict())))
            db.execute('UPDATE asset_watches SET event_count=event_count+1 WHERE watch_id=?', (w['watch_id'],))
            db.execute('UPDATE sessions SET heartbeat=?,event_count=event_count+1 WHERE session_id=?', (receipt, w['watch_id']))
            lo = (event.time-timedelta(seconds=w['horizon_seconds'])).isoformat()
            hi = (event.time+timedelta(seconds=w['horizon_seconds'])).isoformat()
            candidates = [Event(**json.loads(r[0])) for r in db.execute('SELECT payload FROM watch_events WHERE watch_id=? AND generation=? AND timestamp>=? AND timestamp<=? ORDER BY timestamp,event_id', (w['watch_id'], w['generation'], lo, hi))]
            videos = [event] if event.modality == 'video' else [e for e in candidates if e.modality == 'video']
            for video in videos:
                lo = (video.time-timedelta(seconds=w['horizon_seconds'])).isoformat()
                hi = (video.time+timedelta(seconds=w['horizon_seconds'])).isoformat()
                signals = [Event(**json.loads(r[0])) for r in db.execute('SELECT payload FROM watch_events WHERE watch_id=? AND generation=? AND source_session=? AND timestamp>=? AND timestamp<=? ORDER BY timestamp,event_id', (w['watch_id'], w['generation'], w['http_stream'], lo, hi))]
                if not signals: continue
                report = _report(w, video, signals, receipt)
                iid = w['watch_id']+':'+report['incident_id']
                old = db.execute('SELECT first_report FROM incidents WHERE incident_id=?', (iid,)).fetchone()
                if old: report['detected_at'] = json.loads(old['first_report'])['detected_at']
                history._save_incident(db, w['watch_id'], report)


class WatchStore:
    def __init__(self, root, accounts_path=None):
        from pathlib import Path
        from .accounts import Accounts
        from .history import History
        self.root = Path(root).resolve()
        self.accounts = Accounts(accounts_path or self.root/'runs/accounts.sqlite3')
        self.history = History(self.root/'runs/history.sqlite3')

    def installation_id(self, token):
        self.accounts.require(token,'read')
        with self.history.connect() as db:
            return db.execute('SELECT installation_id FROM watch_site WHERE singleton=1').fetchone()[0]

    def open(self, token, camera_session, http_stream, horizon_seconds=10.):
        from .history import ACTIVE, utc_now
        actor = self.accounts.require(token, 'analyse')['username']
        if type(horizon_seconds) not in (int, float) or not math.isfinite(horizon_seconds) or not 1 <= horizon_seconds <= 30:
            raise ValueError('Choose a correlation window from 1 to 30 seconds')
        tick = utc_now();wid = uuid.uuid4().hex
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            camera = db.execute('SELECT * FROM sessions WHERE session_id=?', (camera_session,)).fetchone()
            stream = db.execute('SELECT * FROM sessions WHERE session_id=?', (http_stream,)).fetchone()
            if not camera or camera['mode'] not in ('webcam', 'rtsp', 'file_replay') or camera['state'] not in ACTIVE or camera['stop_requested']:
                raise ValueError('Choose an active camera session')
            if not stream or stream['mode'] != 'http_stream' or stream['state'] != 'running':
                raise ValueError('Choose an open HTTP stream')
            if camera['asset_id'] != stream['asset_id']: raise ValueError('Camera and HTTP stream must have exactly the same asset ID')
            if db.execute("SELECT 1 FROM asset_watches WHERE state='active' AND (camera_session=? OR http_stream=?)", (camera_session, http_stream)).fetchone():
                raise ValueError('A selected source is already linked; close its watch first')
            if db.execute("SELECT COUNT(*) FROM asset_watches WHERE state='active'").fetchone()[0] >= 10: raise ValueError('Maximum 10 active asset watches')
            db.execute("INSERT INTO sessions(session_id,camera_id,asset_id,mode,state,started_at,heartbeat,config) VALUES(?,?,?,'asset_watch','watching',?,?,?)", (wid, 'watch:'+wid, camera['asset_id'], tick, tick, encode({'horizon_seconds': horizon_seconds})))
            db.execute("INSERT INTO asset_watches(watch_id,camera_session,http_stream,asset_id,horizon_seconds,state,created_at,created_by,generation_started_at) VALUES(?,?,?,?,?,'active',?,?,?)", (wid, camera_session, http_stream, camera['asset_id'], horizon_seconds, tick, actor, tick))
            _audit(db, wid, tick, actor, 'asset_watch_opened', 'Selected sources linked prospectively; clocks and physical mapping are operator declarations.', 'authenticated_account')
        return self.get(token, wid)

    def get(self, token, wid):
        self.accounts.require(token, 'read')
        with self.history.connect() as db:
            r = db.execute('SELECT * FROM asset_watches WHERE watch_id=?', (wid,)).fetchone()
            if not r: raise ValueError('Asset watch not found')
            return dict(r)

    def watches(self, token):
        self.accounts.require(token, 'read')
        with self.history.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM asset_watches ORDER BY state='active' DESC,created_at DESC LIMIT 100")]

    def close(self, token, wid):
        from .history import utc_now
        actor = self.accounts.require(token, 'analyse')['username']
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            w = db.execute('SELECT * FROM asset_watches WHERE watch_id=?', (wid,)).fetchone()
            if not w: raise ValueError('Asset watch not found')
            if w['state'] == 'active': _end(db, w, utc_now(), 'operator_closed', actor, 'authenticated_account')
        return self.get(token, wid)

    def export(self, token, wid):
        self.accounts.require(token, 'read')
        with self.history.connect() as db:
            db.execute('BEGIN')
            w = db.execute('SELECT * FROM asset_watches WHERE watch_id=?', (wid,)).fetchone()
            if not w: raise ValueError('Asset watch not found')
            events = [{**dict(r), 'payload':json.loads(r['payload'])} for r in db.execute('SELECT * FROM watch_events WHERE watch_id=? ORDER BY rowid', (wid,))]
            reports = [{**dict(r), 'first_report':json.loads(r['first_report']), 'current_report':json.loads(r['current_report'])} for r in db.execute('SELECT * FROM incidents WHERE session_id=? ORDER BY incident_id', (wid,))]
            audit = [dict(r) for r in db.execute('SELECT * FROM audit WHERE session_id=? ORDER BY audit_id', (wid,))]
            provenance = db.execute('SELECT b.baseline_id,b.sha256,b.metadata FROM stream_baselines b JOIN live_streams s ON s.baseline_id=b.baseline_id WHERE s.stream_id=?', (w['http_stream'],)).fetchone()
            inputs = {}
            for e in events:
                if e['source_session'] != w['http_stream']: continue
                # These are locally generated stream evidence references, not file paths.
                identifier = e['payload']['evidence_ref'].rsplit('/', 1)[-1]
                record = db.execute('SELECT * FROM stream_inputs WHERE stream_id=? AND message_id=?', (w['http_stream'], identifier)).fetchone()
                if not record: continue
                keys = json.loads(record['result']).get('confirmation_input_ids', []) or [identifier]
                for key in keys:
                    raw = db.execute('SELECT * FROM stream_inputs WHERE stream_id=? AND message_id=?', (w['http_stream'], key)).fetchone()
                    if raw: inputs[key] = {**dict(raw), 'payload':json.loads(raw['payload']), 'result':json.loads(raw['result'])}
            baseline = None if not provenance else {'baseline_id':provenance['baseline_id'], 'sha256':provenance['sha256'], 'asset_metadata':json.loads(provenance['metadata'])[w['asset_id']]}
        return {'schema_version':'asset-watch-1.0', 'watch':dict(w), 'events':events, 'incidents':reports, 'audit':audit,
                'baseline':baseline, 'supporting_inputs':list(inputs.values()), 'limitations':LIMITATIONS}
