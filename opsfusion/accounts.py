"""Single-installation local accounts. OS/file administrators remain trusted."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import getpass
import hashlib
import hmac
from pathlib import Path
from .runtime import data_root
import re
import secrets
import sqlite3

ROLES=('admin','operator','viewer')
PERMISSIONS={'read':set(ROLES),'review':{'admin','operator'},'capture':{'admin','operator'},
             'analyse':{'admin','operator'},'model_admin':{'admin'},'notify_admin':{'admin'},'users_admin':{'admin'}}
ITERATIONS=600000


def now():
    return datetime.now(timezone.utc).timestamp()


def username(value):
    if not isinstance(value,str):raise ValueError('Username must be text')
    value=value.lower().strip()
    if not re.fullmatch('[a-z0-9_.-]{3,64}',value):
        raise ValueError('Username must be 3–64 letters, digits, underscores, dots or hyphens')
    return value


def password_hash(password,salt):
    return hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,ITERATIONS).hex()


def validate_password(password):
    if not isinstance(password,str) or not 12<=len(password)<=256:
        raise ValueError('Use a password of 12–256 characters')


class Accounts:
    def __init__(self,path,clock=now):
        self.path=Path(path).resolve();self.path.parent.mkdir(parents=True,exist_ok=True);self.clock=clock
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY,role TEXT NOT NULL,
                    salt TEXT NOT NULL,password_hash TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS auth_sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL REFERENCES users(username),
                    created_at REAL NOT NULL,expires_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS login_limits(username TEXT PRIMARY KEY,failures INTEGER NOT NULL,until_time REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS account_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,timestamp REAL NOT NULL,
                    actor TEXT NOT NULL,action TEXT NOT NULL,subject TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA busy_timeout=10000');db.execute('PRAGMA journal_mode=WAL')
        try:
            with db:yield db
        finally:db.close()

    def initialized(self):
        with self.connect() as db:return db.execute('SELECT COUNT(*) FROM users').fetchone()[0]>0

    def bootstrap(self,name,password):
        name=username(name);validate_password(password);salt=secrets.token_bytes(16)
        hashed=password_hash(password,salt)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]:raise ValueError('Accounts already initialized')
            db.execute('INSERT INTO users(username,role,salt,password_hash) VALUES(?,?,?,?)',(name,'admin',salt.hex(),hashed))
            db.execute('INSERT INTO account_audit(timestamp,actor,action,subject) VALUES(?,?,?,?)',(self.clock(),name,'bootstrap',name))

    def login(self,name,password,ttl=28800):
        try:name=username(name)
        except ValueError:name='invalid-input'
        if not isinstance(password,str) or len(password)>256 or not 1<=ttl<=28800:
            raise PermissionError('Sign-in failed')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            tick=self.clock()
            db.execute('DELETE FROM auth_sessions WHERE expires_at<=?',(tick,))
            db.execute('DELETE FROM login_limits WHERE until_time<=?',(tick,))
            limit=db.execute('SELECT * FROM login_limits WHERE username=?',(name,)).fetchone()
            if limit and limit['failures']>=5:raise PermissionError('Sign-in failed; retry later')
            row=db.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
            # A dummy hash keeps unknown-user failures on the same hashing path.
            salt=bytes.fromhex(row['salt']) if row else b'\0'*16
            hashed=password_hash(password,salt)
            success=row and row['enabled'] and hmac.compare_digest(hashed,row['password_hash'])
            if not success:
                db.execute('INSERT INTO login_limits VALUES(?,?,?) ON CONFLICT(username) DO UPDATE SET failures=failures+1',
                           (name,1,tick+300))
                # Do not raise until the transaction commits the failure counter.
            else:
                db.execute('DELETE FROM login_limits WHERE username=?',(name,))
                token=secrets.token_urlsafe(32)
                db.execute('INSERT INTO auth_sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),name,tick,tick+ttl))
                db.execute('INSERT INTO account_audit(timestamp,actor,action,subject) VALUES(?,?,?,?)',(tick,name,'login',name))
        if not success:raise PermissionError('Sign-in failed')
        return token

    def identity(self,token):
        if not isinstance(token,str) or not 20<=len(token)<=256:raise PermissionError('Sign in again')
        digest=hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            row=db.execute('SELECT u.username,u.role,u.enabled,s.expires_at FROM auth_sessions s JOIN users u ON u.username=s.username WHERE token_hash=?',(digest,)).fetchone()
        if not row or not row['enabled'] or row['expires_at']<=self.clock():raise PermissionError('Sign in again')
        return {'username':row['username'],'role':row['role']}

    def require(self,token,action):
        who=self.identity(token)
        if action not in PERMISSIONS or who['role'] not in PERMISSIONS[action]:raise PermissionError('This role cannot perform that action')
        return who

    def logout(self,token):
        if not isinstance(token,str):return
        with self.connect() as db:db.execute('DELETE FROM auth_sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))

    def add_user(self,token,name,password,role):
        actor=self.require(token,'users_admin')['username'];name=username(name);validate_password(password)
        if role not in ROLES:raise ValueError('Choose admin, operator or viewer')
        salt=secrets.token_bytes(16);hashed=password_hash(password,salt)
        try:
            with self.connect() as db:
                db.execute('INSERT INTO users(username,role,salt,password_hash) VALUES(?,?,?,?)',(name,role,salt.hex(),hashed))
                db.execute('INSERT INTO account_audit(timestamp,actor,action,subject) VALUES(?,?,?,?)',(self.clock(),actor,'create_'+role,name))
        except sqlite3.IntegrityError:raise ValueError('Username already exists') from None

    def set_user(self,token,name,role,enabled=True):
        actor=self.require(token,'users_admin')['username'];name=username(name)
        if role not in ROLES or type(enabled)!=bool:raise ValueError('Invalid account settings')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
            if not row:raise ValueError('Account not found')
            if row['role']=='admin' and row['enabled'] and (role!='admin' or not enabled):
                if db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0]<=1:
                    raise ValueError('Keep at least one enabled admin')
            db.execute('UPDATE users SET role=?,enabled=? WHERE username=?',(role,int(enabled),name))
            db.execute('DELETE FROM auth_sessions WHERE username=?',(name,))
            db.execute('INSERT INTO account_audit(timestamp,actor,action,subject) VALUES(?,?,?,?)',(self.clock(),actor,'account_changed',name))

    def change_password(self,token,old_password,new_password):
        who=self.identity(token);validate_password(new_password)
        with self.connect() as db:
            row=db.execute('SELECT * FROM users WHERE username=?',(who['username'],)).fetchone()
            if not isinstance(old_password,str) or len(old_password)>256 or not hmac.compare_digest(password_hash(old_password,bytes.fromhex(row['salt'])),row['password_hash']):
                raise PermissionError('Password change failed')
            salt=secrets.token_bytes(16)
            db.execute('UPDATE users SET salt=?,password_hash=? WHERE username=?',(salt.hex(),password_hash(new_password,salt),who['username']))
            db.execute('DELETE FROM auth_sessions WHERE username=?',(who['username'],))
            db.execute('INSERT INTO account_audit(timestamp,actor,action,subject) VALUES(?,?,?,?)',(self.clock(),who['username'],'password_changed',who['username']))

    def users(self,token):
        self.require(token,'users_admin')
        with self.connect() as db:return [dict(r) for r in db.execute('SELECT username,role,enabled FROM users ORDER BY username')]

    def audit(self,token):
        self.require(token,'users_admin')
        with self.connect() as db:return [dict(r) for r in db.execute('SELECT * FROM account_audit ORDER BY id')]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['bootstrap','add-user','set-user'])
    p.add_argument('--database',type=Path,default=data_root()/'runs/accounts.sqlite3')
    p.add_argument('--username',required=True);p.add_argument('--role',choices=ROLES,default='operator')
    p.add_argument('--admin');p.add_argument('--disable',action='store_true')
    args=p.parse_args();a=Accounts(args.database)
    try:
        if args.command=='bootstrap':
            pw=getpass.getpass('New admin password (12+ characters): ')
            if pw!=getpass.getpass('Confirm password: '):raise ValueError('Passwords do not match')
            a.bootstrap(args.username,pw)
        else:
            if not args.admin:raise ValueError('--admin username is required')
            token=a.login(args.admin,getpass.getpass('Admin password: '))
            try:
                if args.command=='add-user':
                    pw=getpass.getpass('New user password (12+ characters): ')
                    if pw!=getpass.getpass('Confirm password: '):raise ValueError('Passwords do not match')
                    a.add_user(token,args.username,pw,args.role)
                else:a.set_user(token,args.username,args.role,not args.disable)
            finally:a.logout(token)
        print('Account operation completed. No password or token was saved in a report.')
    except (ValueError,PermissionError) as exc:p.error(str(exc))


if __name__=='__main__':main()
