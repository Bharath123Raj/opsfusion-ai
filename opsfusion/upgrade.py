"""Upgrade local code in place, retaining accounts, history and evidence paths."""
import argparse
import json
from pathlib import Path
import shutil
import sqlite3
import uuid
import re

EXCLUDED={'runs','models','videos','backups','__pycache__','.git','.venv','.pytest_cache','.env'}


def _upgrade(project,source=None):
    project=Path(project).resolve();source=Path(source or Path(__file__).resolve().parents[1]).resolve()
    if project==source:raise ValueError('Choose your existing installation folder, not this update source folder')
    version=project/'opsfusion/__init__.py'
    def read_version(root):
        path=root/'opsfusion/__init__.py'
        if not path.is_file():return None
        match=re.search(r"^__version__\s*=\s*['\"]([0-9.]+)['\"]",path.read_text(),re.M)
        return match.group(1) if match else None
    previous=read_version(project);next_version=read_version(source)
    if previous==next_version and next_version in ('0.10.0','0.11.0','0.12.0','0.13.0','0.15.0','0.16.0') and (project/'dashboard.py').is_file():
        return {'project':str(project),'backup':None,'files_updated':0,'run_paths_preserved':True,'from_version':previous,'to_version':next_version,'already_current':True}
    supported={('0.9.0','0.10.0'),('0.9.0','0.11.0'),('0.10.0','0.11.0'),('0.9.0','0.12.0'),('0.10.0','0.12.0'),('0.11.0','0.12.0'),('0.9.0','0.13.0'),('0.10.0','0.13.0'),('0.11.0','0.13.0'),('0.12.0','0.13.0'),('0.9.0','0.15.0'),('0.10.0','0.15.0'),('0.11.0','0.15.0'),('0.12.0','0.15.0'),('0.13.0','0.15.0')}
    supported.update((v,'0.16.0') for v in ('0.9.0','0.10.0','0.11.0','0.12.0','0.13.0','0.15.0'))
    if not (project/'dashboard.py').is_file() or (previous,next_version) not in supported:
        raise ValueError('Target must be a supported Step9–Step15 installation containing dashboard.py')
    history=project/'runs/history.sqlite3'
    if history.is_file():
        db=sqlite3.connect(history.as_uri()+'?mode=ro',uri=True)
        try:
            active=db.execute("SELECT COUNT(*) FROM sessions WHERE state IN ('starting','running','reconnecting','stopping')").fetchone()[0]
            if active:raise ValueError('Close HTTP streams and stop/recover camera workers before upgrading; also stop Streamlit and the API')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='deliveries'").fetchone():
                sending=db.execute("SELECT COUNT(*) FROM deliveries WHERE state='in_flight'").fetchone()[0]
                if sending:raise ValueError('Stop the notification worker and finish/recover in-flight deliveries before upgrading')
        finally:db.close()
    files=[]
    for p in sorted(source.rglob('*')):
        relative=p.relative_to(source)
        if not p.is_file() or any(x in EXCLUDED or x.startswith('.env.') for x in relative.parts):continue
        if p.suffix.lower() in ('.pyc','.pt','.joblib','.sqlite','.sqlite3','.png','.jpg','.jpeg'):continue
        destination=project/relative
        try:destination.resolve().relative_to(project)
        except ValueError:raise ValueError('Target code path points outside the project') from None
        if destination.is_dir():raise ValueError('A target code filename is a directory')
        files.append((p,destination,relative))
    backup=project/'runs/code-backups'/(previous+'-to-'+next_version+'-'+uuid.uuid4().hex)
    backup.mkdir(parents=True)
    before=[];copied=[]
    # Complete backup before making the new code concrete.
    for _,destination,relative in files:
        if destination.is_file():
            saved=backup/relative;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(destination,saved)
            before.append(str(relative))
    manifest={'status':'prepared','from_version':previous,'to_version':next_version,
              'project':str(project),'backed_up_files':before,'changed_files':[str(r) for _,_,r in files],
              'runs_models_videos_preserved':True}
    record=backup/'upgrade_manifest.json';record.write_text(json.dumps(manifest,indent=2))
    try:
        for src,destination,relative in files:
            destination.parent.mkdir(parents=True,exist_ok=True);copied.append((destination,relative))
            shutil.copy2(src,destination)
    except BaseException:
        for destination,relative in reversed(copied):
            saved=backup/relative
            if saved.is_file():shutil.copy2(saved,destination)
            elif destination.is_file():destination.unlink()
        manifest['status']='rolled_back';record.write_text(json.dumps(manifest,indent=2))
        raise
    manifest['status']='completed';record.write_text(json.dumps(manifest,indent=2))
    return {'project':str(project),'backup':str(backup),'files_updated':len(files),'run_paths_preserved':True,'from_version':previous,'to_version':next_version}


def upgrade(project,source=None):
    project=Path(project).resolve()
    if (project/'runs/.service.lock').is_file():
        from .service import exclusive
        with exclusive(project):return _upgrade(project,source)
    return _upgrade(project,source)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',type=Path,required=True)
    args=p.parse_args()
    try:
        result=upgrade(args.project)
        print(('ALREADY CURRENT: ' if result.get('already_current') else 'UPGRADED: ')+result['project'])
        if result['backup']:print('CODE BACKUP: '+result['backup'])
        print('Accounts, run data and evidence paths preserved. Open PowerShell in the upgraded folder; sign in with your existing account.')
    except (ValueError,OSError,sqlite3.Error) as exc:p.error(str(exc))


if __name__=='__main__':main()
