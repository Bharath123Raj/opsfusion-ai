"""Durable preview-first HTTPS webhook and TLS SMTP outbox. No destination is bundled."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit

from .accounts import Accounts
from .history import History
from .email_delivery import EmailDeliveryError, email_settings, email_target_hash, send_email


def target_url(env_name,allow_loopback_http=False):
    if not re.fullmatch('OPSFUSION_[A-Z0-9_]{1,80}',env_name):
        raise ValueError('Use an OPSFUSION_ environment variable name')
    url=os.environ.get(env_name,'')
    try:
        parts=urlsplit(url)
        allowed=parts.scheme=='https' or (allow_loopback_http and parts.scheme=='http' and parts.hostname in ('127.0.0.1','localhost','::1'))
        if not allowed or not parts.hostname or parts.username or parts.password or parts.fragment or len(url)>2048 or any(c in url for c in '\r\n\x00'):
            raise ValueError()
        parts.port
    except ValueError:raise ValueError('Configured webhook must be a valid HTTPS URL without user-info or fragment') from None
    return url


def target_hash(url):return hashlib.sha256(url.encode()).hexdigest()


class Outbox:
    def __init__(self,history,accounts,clock=time.time,allow_loopback_http=False):
        self.history=history;self.accounts=accounts;self.clock=clock;self.allow_loopback_http=allow_loopback_http
        with history.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS notification_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS destinations(name TEXT PRIMARY KEY,env_name TEXT,
                    enabled INTEGER NOT NULL,accept_synthetic INTEGER NOT NULL DEFAULT 0,
                    revision INTEGER NOT NULL DEFAULT 1,cursor INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,destination TEXT NOT NULL,
                    destination_revision INTEGER NOT NULL,notice_id INTEGER NOT NULL,payload TEXT NOT NULL,
                    state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_at REAL NOT NULL DEFAULT 0,
                    target_hash TEXT,lease_token TEXT,lease_until REAL,status_code INTEGER,error_code TEXT,
                    created_at REAL NOT NULL,updated_at REAL NOT NULL,
                    UNIQUE(destination,destination_revision,notice_id));
                CREATE TABLE IF NOT EXISTS delivery_attempts(id INTEGER PRIMARY KEY AUTOINCREMENT,
                    delivery_id TEXT NOT NULL,attempt INTEGER NOT NULL,timestamp REAL NOT NULL,
                    status_code INTEGER,error_code TEXT);
                CREATE TABLE IF NOT EXISTS notification_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,actor TEXT NOT NULL,action TEXT NOT NULL,subject TEXT NOT NULL);
                INSERT OR IGNORE INTO destinations(name,env_name,enabled,accept_synthetic) VALUES('Preview',NULL,0,1);
            ''')
            db.execute('BEGIN IMMEDIATE')
            for table in ('destinations','deliveries'):
                columns={r['name'] for r in db.execute('PRAGMA table_info('+table+')')}
                if 'channel' not in columns:
                    db.execute("ALTER TABLE "+table+" ADD COLUMN channel TEXT NOT NULL DEFAULT 'webhook'")
            db.execute("INSERT OR IGNORE INTO notification_meta VALUES('installation_id',?)",(secrets.token_hex(16),))

    def configure(self,token,name,env_name,enabled=False,accept_synthetic=False,channel='webhook'):
        actor=self.accounts.require(token,'notify_admin')['username']
        if not isinstance(name,str) or not re.fullmatch('[A-Za-z0-9_-]{3,64}',name) or name=='Preview' or type(enabled)!=bool or type(accept_synthetic)!=bool:
            raise ValueError('Use a destination name of 3–64 letters, digits, underscores or hyphens')
        if not re.fullmatch('OPSFUSION_[A-Z0-9_]{1,80}',env_name):raise ValueError('Use an OPSFUSION_ environment variable')
        if channel not in ('webhook','email'):raise ValueError('Choose webhook or email')
        if enabled:self._target(channel,env_name)
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT * FROM destinations WHERE name=?',(name,)).fetchone()
            revision=current['revision']+1 if current else 1
            cursor=db.execute('SELECT COALESCE(MAX(notice_id),0) FROM notices').fetchone()[0]
            db.execute('INSERT INTO destinations(name,env_name,enabled,accept_synthetic,revision,cursor,channel) VALUES(?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET env_name=excluded.env_name,enabled=excluded.enabled,accept_synthetic=excluded.accept_synthetic,revision=excluded.revision,cursor=excluded.cursor,channel=excluded.channel',
                       (name,env_name,int(enabled),int(accept_synthetic),revision,cursor,channel))
            # A changed destination cannot inherit previously authorized deliveries.
            db.execute("UPDATE deliveries SET state='cancelled',error_code='destination_changed',updated_at=? WHERE destination=? AND state IN ('pending','in_flight')",(self.clock(),name))
            db.execute('INSERT INTO notification_audit(timestamp,actor,action,subject) VALUES(?,?,?,?)',
                       (self.clock(),actor,'enabled' if enabled else 'paused',name))

    def _target(self,channel,env_name):
        if channel=='email':
            config=email_settings(env_name)
            return config,email_target_hash(config)
        url=target_url(env_name,self.allow_loopback_http)
        return url,target_hash(url)

    def queue_test_email(self,token,name):
        actor=self.accounts.require(token,'notify_admin')['username']
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            dest=db.execute('SELECT * FROM destinations WHERE name=?',(name,)).fetchone()
            if dest is None or dest['channel']!='email' or not dest['enabled']:
                raise ValueError('Choose an enabled email destination')
            # A bounded pending probe per destination avoids duplicate clicks during a rerun.
            existing=db.execute("SELECT id FROM deliveries WHERE destination=? AND destination_revision=? AND notice_id<0 AND state IN ('pending','in_flight')",(name,dest['revision'])).fetchone()
            if existing:return existing['id']
            _,endpoint=self._target('email',dest['env_name'])
            installation=db.execute("SELECT value FROM notification_meta WHERE key='installation_id'").fetchone()[0]
            identifier=hashlib.sha256(f"{installation}:{name}:test:{secrets.token_hex(16)}".encode()).hexdigest()
            tick=self.clock()
            payload={'schema_version':'notification-test-1.0','kind':'test','delivery_id':identifier,
                     'requested_by':actor,'requested_at':tick,'message':'Email connection test. No incident detected.'}
            # Negative IDs belong to explicit probes, separate from positive incident notice IDs.
            notice_id=-1-secrets.randbelow(2**62)
            db.execute("INSERT INTO deliveries(id,destination,destination_revision,notice_id,payload,state,target_hash,created_at,updated_at,channel) VALUES(?,?,?,?,?,'pending',?,?,?,'email')",
                       (identifier,name,dest['revision'],notice_id,json.dumps(payload,sort_keys=True),endpoint,tick,tick))
            db.execute('INSERT INTO notification_audit(timestamp,actor,action,subject) VALUES(?,?,?,?)',
                       (tick,actor,'test_email_queued',name))
        return identifier

    def destinations(self,token):
        self.accounts.require(token,'read')
        with self.history.connect() as db:return [dict(r) for r in db.execute('SELECT * FROM destinations ORDER BY name')]

    def sync(self,token):
        self.accounts.require(token,'notify_admin')
        total=0
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            installation=db.execute("SELECT value FROM notification_meta WHERE key='installation_id'").fetchone()[0]
            for dest in db.execute("SELECT * FROM destinations WHERE enabled=1 OR name='Preview'").fetchall():
                notices=db.execute('SELECT * FROM notices WHERE notice_id>? ORDER BY notice_id LIMIT 100',(dest['cursor'],)).fetchall()
                for notice in notices:
                    identifier=hashlib.sha256(f"{installation}:{dest['name']}:{dest['revision']}:{notice['notice_id']}".encode()).hexdigest()
                    snapshot=json.loads(notice['payload']) if notice['payload'] else None
                    state='preview' if dest['name']=='Preview' else 'pending';error=None;endpoint=None
                    if snapshot is None:
                        state='suppressed';error='legacy_snapshot_unavailable';payload={'schema_version':'notification-1.0','delivery_id':identifier,'notice_id':notice['notice_id']}
                    else:
                        payload={'schema_version':'notification-1.0','delivery_id':identifier,'notice_id':notice['notice_id'],
                                 'reason':notice['reason'],'observed_at':notice['timestamp'],
                                 'incident':{k:snapshot[k] for k in ('incident_id','asset_id','type','risk','detected_at','synthetic')},
                                 'cause_status':snapshot['hypothesis']['status'],'evidence_ids':[f['event_id'] for f in snapshot['observed_facts']],
                                 'automatic_action_executed':False}
                        if dest['name']!='Preview':
                            if snapshot['synthetic'] and not dest['accept_synthetic']:
                                state='suppressed';error='synthetic_source_blocked'
                            else:
                                try:_,endpoint=self._target(dest['channel'],dest['env_name'])
                                except ValueError:state='failed';error='destination_not_configured'
                    tick=self.clock()
                    db.execute('INSERT OR IGNORE INTO deliveries(id,destination,destination_revision,notice_id,payload,state,target_hash,error_code,created_at,updated_at,channel) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                               (identifier,dest['name'],dest['revision'],notice['notice_id'],json.dumps(payload,sort_keys=True),state,endpoint,error,tick,tick,dest['channel']))
                    total+=1
                if notices:db.execute('UPDATE destinations SET cursor=? WHERE name=?',(notices[-1]['notice_id'],dest['name']))
        return total

    def rows(self,token,limit=100):
        self.accounts.require(token,'read')
        with self.history.connect() as db:
            values=[dict(r) for r in db.execute('SELECT * FROM deliveries ORDER BY created_at DESC,id LIMIT ?',(limit,))]
        for row in values:
            row['payload']=json.loads(row['payload']);row.pop('lease_token',None)
        return values

    def _claim(self,token):
        self.accounts.require(token,'notify_admin')
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE');tick=self.clock()
            db.execute("UPDATE deliveries SET state=CASE WHEN attempts>=4 THEN 'failed' ELSE 'pending' END,error_code='delivery_uncertain',lease_token=NULL WHERE state='in_flight' AND lease_until<=?",(tick,))
            row=db.execute("SELECT q.* FROM deliveries q JOIN destinations d ON d.name=q.destination WHERE q.state='pending' AND q.next_at<=? AND d.enabled=1 AND d.revision=q.destination_revision ORDER BY q.created_at,q.id LIMIT 1",(tick,)).fetchone()
            if row is None:return None
            result=dict(row);result['attempts']+=1;result['lease_token']=secrets.token_hex(16)
            db.execute("UPDATE deliveries SET state='in_flight',attempts=?,lease_token=?,lease_until=?,updated_at=? WHERE id=?",(result['attempts'],result['lease_token'],tick+60,tick,result['id']))
            return result

    def dispatch_one(self,token,transport=None,email_transport=None):
        row=self._claim(token)
        if row is None:return False
        code=None;error=None;email_retry=False
        try:
            # Recheck authorization/configuration immediately before transport.
            self.accounts.require(token,'notify_admin')
            with self.history.connect() as db:dest=db.execute('SELECT * FROM destinations WHERE name=?',(row['destination'],)).fetchone()
            if not dest['enabled'] or dest['revision']!=row['destination_revision']:
                error='destination_changed'
            else:
                target,endpoint=self._target(dest['channel'],dest['env_name'])
                if endpoint!=row['target_hash']:error='endpoint_changed'
                else:
                    payload=json.loads(row['payload'])
                    if dest['channel']=='email':code=(email_transport or send_email)(target,payload,row['id'])
                    else:code=(transport or send_webhook)(target,payload,row['id'])
                    if type(code)!=int or not 100<=code<=599:raise RuntimeError('Invalid response code')
        except EmailDeliveryError as exc:
            code=exc.status;error=exc.code;email_retry=exc.retryable
        except PermissionError:error='authorization_revoked'
        except ValueError:error='destination_not_configured'
        except Exception:error='transport_failed'
        if row['channel']=='email':
            retry=email_retry or error=='transport_failed' or error is None and code is not None and 400<=code<500
        else:retry=(error=='transport_failed' or code==429 or code is not None and code>=500)
        state='delivered' if error is None and code is not None and 200<=code<300 else 'pending' if retry and row['attempts']<4 else 'failed'
        if error is None and state!='delivered':
            error='smtp_rejected' if row['channel']=='email' else 'redirect_rejected' if code and 300<=code<400 else 'http_rejected'
        tick=self.clock();next_at=tick+min(300,5*2**(row['attempts']-1)) if state=='pending' else 0
        with self.history.connect() as db:
            db.execute('INSERT INTO delivery_attempts(delivery_id,attempt,timestamp,status_code,error_code) VALUES(?,?,?,?,?)',(row['id'],row['attempts'],tick,code,error))
            db.execute('UPDATE deliveries SET state=?,next_at=?,status_code=?,error_code=?,lease_token=NULL,updated_at=? WHERE id=? AND state=\'in_flight\' AND lease_token=?',
                       (state,next_at,code,error,tick,row['id'],row['lease_token']))
        return True

    def export(self,token):
        self.accounts.require(token,'read')
        with self.history.connect() as db:
            db.execute('BEGIN')
            rows=[dict(r) for r in db.execute('SELECT * FROM deliveries ORDER BY created_at DESC,id')]
            for row in rows:
                row['payload']=json.loads(row['payload']);row.pop('lease_token',None)
            return {'schema_version':'outbox-1.0','deliveries':rows,
                    'attempts':[dict(r) for r in db.execute('SELECT * FROM delivery_attempts ORDER BY id')],
                    'configuration_audit':[dict(r) for r in db.execute('SELECT * FROM notification_audit ORDER BY id')],
                    'limitations':['At-least-once delivery; receiver must deduplicate Idempotency-Key.',
                                   'Delivered means HTTP 2xx or SMTP acceptance, not inbox placement/human receipt.',
                                   'Email uses a stable Message-ID, but SMTP does not guarantee duplicate suppression.',
                                   'Endpoint URLs, email routing/credentials and response bodies are excluded.']}


def send_webhook(url,payload,identifier):
    import httpx
    # Explicitly disable redirects and environment proxy inheritance.
    with httpx.Client(timeout=5,follow_redirects=False,trust_env=False) as client:
        with client.stream('POST',url,json=payload,headers={'Idempotency-Key':identifier}) as response:
            return response.status_code


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['preview','work']);p.add_argument('--username',required=True)
    p.add_argument('--database',type=Path,default=Path('runs/history.sqlite3'))
    p.add_argument('--accounts',type=Path,default=Path('runs/accounts.sqlite3'))
    p.add_argument('--max-seconds',type=int,default=3600)
    args=p.parse_args();a=Accounts(args.accounts);token=None
    try:
        if not 1<=args.max_seconds<=86400:raise ValueError('Duration must be 1–86400 seconds')
        token=a.login(args.username,getpass.getpass('Admin password: '));a.require(token,'notify_admin')
        outbox=Outbox(History(args.database),a)
        if args.command=='preview':
            outbox.sync(token);print('Queue built. Preview entries never send; enabled destinations may have pending entries.');return
        started=time.monotonic()
        while time.monotonic()-started<args.max_seconds:
            a.require(token,'notify_admin');outbox.sync(token)
            if not outbox.dispatch_one(token):time.sleep(.5)
    except KeyboardInterrupt:print('Notification worker stopped')
    except (ValueError,PermissionError) as exc:p.error(str(exc))
    finally:
        if token:a.logout(token)


if __name__=='__main__':main()
