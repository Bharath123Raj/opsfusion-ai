"""Offline backups, guarded restore to a new directory, and backup retention."""
import argparse
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile

from . import __version__
from .runtime import data_root
from .service import exclusive

FORMAT='opsfusion-backup-1'
MAX_BYTES=2*1024**3
MAX_FILES=20000


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def _quiescent(root):
    path=Path(root)/'runs/history.sqlite3'
    if not path.exists():return
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute("SELECT COUNT(*) FROM sessions WHERE state IN ('starting','running','reconnecting','stopping','watching')").fetchone()[0]:
            raise ValueError('Close HTTP streams and asset watches; stop/recover camera workers before backup')
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='deliveries'").fetchone():
            if db.execute("SELECT COUNT(*) FROM deliveries WHERE state='in_flight'").fetchone()[0]:raise ValueError('Stop notification delivery and recover in-flight records before backup')


def _strip_sessions(db_path):
    if db_path.name!='accounts.sqlite3':return
    # A SQLite context manager commits, but does not close its connection.
    # Close before hashing: a later WAL checkpoint could otherwise change the
    # main database bytes between manifest creation and ZIP serialization.
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute('DELETE FROM auth_sessions');db.execute('DELETE FROM login_limits')


def backup(root,output_dir,offline=False,include_videos=False):
    if not offline:raise ValueError('Stop standalone servers/workers, then use --offline to attest they are stopped')
    root=Path(root).resolve();out=Path(output_dir).resolve()
    if not root.is_dir():raise ValueError('Data directory does not exist')
    for name in ('runs','models','videos'):
        try:out.relative_to(root/name)
        except ValueError:pass
        else:raise ValueError('Backup output must be outside runs/models/videos')
    with exclusive(root):
        _quiescent(root);out.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='opsfusion-backup-stage-') as td:
            stage=Path(td);entries=[];total=0
            for folder in ('runs','models',*(['videos'] if include_videos else [])):
                source=root/folder
                if not source.exists():continue
                if source.is_symlink():raise ValueError('Backup sources cannot be symlinks')
                for path in sorted(source.rglob('*')):
                    rel=path.relative_to(root)
                    if 'code-backups' in rel.parts or '__pycache__' in rel.parts:continue
                    if path.is_symlink():raise ValueError('Backup sources cannot contain symlinks')
                    if not path.is_file():continue
                    if path.name in ('.service.lock','service-state.json') or path.name.endswith(('-wal','-shm','.tmp','.pyc')) or path.name.startswith('.env'):continue
                    size=path.stat().st_size;total+=size
                    if total>MAX_BYTES or len(entries)>=MAX_FILES:raise ValueError('Backup exceeds the 2 GiB / 20,000 file limit')
                    dest=stage/rel;dest.parent.mkdir(parents=True,exist_ok=True)
                    if path.suffix in ('.sqlite3','.sqlite'):
                        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(dest)) as dst:
                            src.backup(dst)
                        _strip_sessions(dest)
                    else:shutil.copy2(path,dest)
                    entries.append({'path':rel.as_posix(),'bytes':dest.stat().st_size,'sha256':sha(dest)})
            if not entries:raise ValueError('No persistent data to back up')
            manifest={'format':FORMAT,'application_version':__version__,'created_at':datetime.now(timezone.utc).isoformat(),
                      'origin':root.as_uri(),'auth_sessions_removed':True,'offline_operator_attestation':True,
                      'includes_videos_folder':include_videos,'files':entries}
            name='opsfusion-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]+'.zip'
            target=out/name;temporary=out/(name+'.tmp')
            try:
                with os.fdopen(os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as raw:
                    with zipfile.ZipFile(raw,'w',zipfile.ZIP_DEFLATED) as z:
                        z.writestr('manifest.json',json.dumps(manifest,indent=2))
                        for item in entries:z.write(stage/item['path'],item['path'])
                temporary.replace(target)
            finally:temporary.unlink(missing_ok=True)
            return {'archive':str(target),'files':len(entries),'auth_sessions_removed':True,'sha256':sha(target)}


def _manifest(z):
    if len(z.infolist())>MAX_FILES+1:raise ValueError('Too many backup members')
    names=z.namelist()
    if len(names)!=len(set(names)):raise ValueError('Duplicate backup member')
    try:
        info=z.getinfo('manifest.json')
        if info.file_size>8*1024*1024:raise ValueError('Manifest too large')
        value=json.loads(z.read(info))
        if value['format']!=FORMAT or not isinstance(value['files'],list) or not value['files']:raise ValueError()
        entries={};total=0
        for item in value['files']:
            name=item['path'];parts=PurePosixPath(name).parts
            if not isinstance(name,str) or '\\' in name or not parts or parts[0] not in ('runs','models','videos') or '..' in parts or name.startswith('/') or name!=PurePosixPath(name).as_posix() or name in entries:raise ValueError()
            if type(item['bytes'])!=int or item['bytes']<0 or not isinstance(item['sha256'],str) or len(item['sha256'])!=64:raise ValueError()
            member=z.getinfo(name);total+=member.file_size
            if stat.S_ISLNK(member.external_attr>>16) or member.is_dir() or member.file_size!=item['bytes'] or total>MAX_BYTES:raise ValueError()
            entries[name]=item
        if set(names)!={'manifest.json',*entries}:raise ValueError()
        return value,entries
    except (KeyError,TypeError,ValueError,UnicodeError,json.JSONDecodeError):raise ValueError('Invalid or unsafe OpsFusion backup manifest') from None


def restore(archive,target,offline=False):
    if not offline:raise ValueError('Use --offline; restore is a local filesystem administration operation')
    target=Path(target).resolve()
    if target.exists() and any(target.iterdir()):raise ValueError('Restore target must be a NEW or empty data directory; existing data is never overwritten')
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='opsfusion-restore-',dir=target.parent) as td:
        stage=Path(td)
        with zipfile.ZipFile(archive) as z:
            manifest,entries=_manifest(z)
            for name,item in entries.items():
                path=stage/name;path.parent.mkdir(parents=True,exist_ok=True);h=hashlib.sha256();size=0
                with z.open(name) as src,path.open('wb') as dst:
                    while chunk:=src.read(1024*1024):
                        size+=len(chunk)
                        if size>item['bytes']:raise ValueError('Backup member exceeds its declared size')
                        h.update(chunk);dst.write(chunk)
                if size!=item['bytes'] or h.hexdigest()!=item['sha256']:raise ValueError('Backup content hash mismatch')
                if path.suffix in ('.sqlite3','.sqlite'):
                    with closing(sqlite3.connect(path)) as db:
                        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Backup database integrity check failed')
                    _strip_sessions(path)
        origins=stage/'runs/restore-origins.json'
        old=json.loads(origins.read_text()).get('origins',[]) if origins.exists() else []
        if not isinstance(old,list) or len(old)>99 or any(not isinstance(x,str) for x in old):raise ValueError('Invalid or excessive evidence origin registry')
        from urllib.parse import urlsplit
        origin=manifest.get('origin','')
        if not isinstance(origin,str) or urlsplit(origin).scheme!='file' or urlsplit(origin).netloc:raise ValueError('Invalid backup origin')
        origins.parent.mkdir(parents=True,exist_ok=True)
        origins.write_text(json.dumps({'format':'evidence-roots-1','origins':list(dict.fromkeys([origin,*old]))},indent=2))
        (stage/'runs/restore-record.json').write_text(json.dumps({'backup_sha256':sha(archive),'restored_at':datetime.now(timezone.utc).isoformat(),'auth_sessions_removed':True,'version':__version__},indent=2))
        # Recheck before the atomic rename; never replace a populated destination.
        if target.exists():
            if any(target.iterdir()):raise ValueError('Restore target changed; existing data is never overwritten')
            target.rmdir()
        stage.rename(target)
    return {'data_root':str(target),'files':len(entries),'auth_sessions_removed':True}


def retention(folder,keep=5,apply=False):
    if type(keep)!=int or not 1<=keep<=100:raise ValueError('Keep must be 1–100 backups')
    folder=Path(folder).resolve();managed=[]
    for path in sorted(folder.glob('opsfusion-backup-*.zip'),key=lambda p:p.name,reverse=True):
        if path.is_symlink():continue
        try:
            with zipfile.ZipFile(path) as z:_manifest(z)
        except (ValueError,OSError,zipfile.BadZipFile):continue
        managed.append(path)
    selected=managed[keep:]
    if apply:
        for path in selected:path.unlink()
    return {'applied':apply,'kept':min(keep,len(managed)),'selected':[p.name for p in selected]}


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('backup');q.add_argument('--project',type=Path,default=data_root());q.add_argument('--output-dir',type=Path,required=True);q.add_argument('--offline',action='store_true');q.add_argument('--include-videos',action='store_true')
    q=sub.add_parser('restore');q.add_argument('--archive',type=Path,required=True);q.add_argument('--target',type=Path,required=True);q.add_argument('--offline',action='store_true')
    q=sub.add_parser('retention');q.add_argument('--folder',type=Path,required=True);q.add_argument('--keep',type=int,default=5);q.add_argument('--apply',action='store_true')
    a=p.parse_args()
    try:
        result=backup(a.project,a.output_dir,a.offline,a.include_videos) if a.command=='backup' else restore(a.archive,a.target,a.offline) if a.command=='restore' else retention(a.folder,a.keep,a.apply)
        print(json.dumps(result,indent=2))
    except (ValueError,OSError,sqlite3.Error,zipfile.BadZipFile) as exc:p.error(str(exc))


if __name__=='__main__':main()
