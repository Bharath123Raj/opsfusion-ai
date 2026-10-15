import copy
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient
from opsfusion.access import settings
from opsfusion.accounts import Accounts
from opsfusion.api import create_app
from opsfusion.core import Event
from opsfusion.history import History
from opsfusion.live import settings as capture_settings
from opsfusion.live_dashboard import evidence_path
from opsfusion.maintenance import backup,restore,retention
from opsfusion.runtime import data_root
from opsfusion.service import commands,exclusive
from opsfusion.upgrade import upgrade

PW='Deployment fixture password 123!'
ZONE={'asset_id':'fixture-asset','camera_id':'fixture-camera','polygon':[[.5,.1],[.9,.1],[.9,.95],[.5,.95]],'confirm_samples':2,'max_age_seconds':1,'min_iou':.15}


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'OPSFUSION_ACCESS_MODE':'local','OPSFUSION_PUBLIC_ORIGIN':'http://127.0.0.1:18810'});self.env.start();self.addCleanup(self.env.stop)
        self.a=Accounts(self.root/'runs/accounts.sqlite3');self.a.bootstrap('fixture-admin',PW)
        self.client=TestClient(create_app(project_root=self.root),follow_redirects=False);self.addCleanup(self.client.close)

    def form(self,path='/access/login'):
        page=self.client.get(path);return re.search('name="csrf" value="([^"]+)"',page.text)[1]

    def login(self,password=PW):
        csrf=self.form();return self.client.post('/access/login',data={'csrf':csrf,'username':'fixture-admin','password':password},headers={'Origin':'http://127.0.0.1:18810'})

    def test_login_sets_private_cookie_and_no_store(self):
        response=self.login();self.assertEqual(response.status_code,303);cookie=response.headers['set-cookie']
        self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=lax',cookie)
        self.assertIn('no-store',response.headers['cache-control'])
        self.assertEqual(self.client.get('/access/verify').status_code,204)

    def test_anonymous_media_denied_and_root_redirected(self):
        self.assertEqual(self.client.get('/access/verify',headers={'X-Forwarded-Uri':'/media/private.jpg'}).status_code,401)
        self.assertEqual(self.client.get('/access/verify',headers={'X-Forwarded-Uri':'/'}).status_code,303)

    def test_cookie_cannot_authorize_api_operations(self):
        self.login();self.assertEqual(self.client.get('/v1/watches').status_code,401)

    def test_bearer_still_authorizes_api(self):
        token=self.a.login('fixture-admin',PW)
        self.assertEqual(self.client.get('/v1/watches',headers={'Authorization':'Bearer '+token}).status_code,200)

    def test_bad_credentials_have_no_echo_or_cookie(self):
        bad='Wrong fixture password 123!';r=self.login(bad)
        self.assertEqual(r.status_code,401);self.assertNotIn(bad,r.text);self.assertNotIn(PW,r.text)
        self.assertNotIn('opsfusion_session=',r.headers['set-cookie'])

    def test_login_requires_same_origin_csrf_and_exact_fields(self):
        csrf=self.form();body={'csrf':csrf,'username':'fixture-admin','password':PW}
        for changed,headers in [({**body,'csrf':'x'*32},{'Origin':'http://127.0.0.1:18810'}),(body,{'Origin':'https://attacker.example'}),(body,{}),({**body,'extra':'value'},{'Origin':'http://127.0.0.1:18810'}),({**body,'csrf':'é'*32},{'Origin':'http://127.0.0.1:18810'})]:
            self.assertEqual(self.client.post('/access/login',data=changed,headers=headers).status_code,403)

    def test_duplicate_fields_and_oversize_form_rejected(self):
        csrf=self.form();head={'Origin':'http://127.0.0.1:18810','Content-Type':'application/x-www-form-urlencoded'}
        self.assertEqual(self.client.post('/access/login',content='csrf='+csrf+'&username=one&username=two&password=x',headers=head).status_code,403)
        self.assertEqual(self.client.post('/access/login',content='x='+'x'*5000,headers=head).status_code,403)

    def test_logout_requires_csrf_and_revokes_cookie_token(self):
        self.login();token=self.client.cookies.get('opsfusion_session')
        self.assertEqual(self.client.post('/access/logout',data={},headers={'Origin':'http://127.0.0.1:18810'}).status_code,403)
        self.assertEqual(self.client.get('/access/verify').status_code,204)
        csrf=self.form('/access/logout')
        self.assertEqual(self.client.post('/access/logout',data={'csrf':csrf},headers={'Origin':'http://127.0.0.1:18810'}).status_code,303)
        with self.assertRaises(PermissionError):self.a.identity(token)
        self.assertEqual(self.client.get('/access/verify').status_code,401)

    def test_account_revocation_immediately_blocks_media(self):
        self.login();old=self.client.cookies.get('opsfusion_session');self.login()
        with self.assertRaises(PermissionError):self.a.identity(old)
        token=self.client.cookies.get('opsfusion_session');self.a.logout(token)
        self.assertEqual(self.client.get('/access/verify').status_code,401)

    def test_https_cookie_uses_secure_host_prefix(self):
        with patch.dict(os.environ,{'OPSFUSION_ACCESS_MODE':'https','OPSFUSION_PUBLIC_ORIGIN':'https://ops.example'}):
            with TestClient(create_app(project_root=self.root),base_url='https://ops.example',follow_redirects=False) as c:
                csrf=re.search('name="csrf" value="([^"]+)"',c.get('/access/login').text)[1]
                r=c.post('/access/login',data={'csrf':csrf,'username':'fixture-admin','password':PW},headers={'Origin':'https://ops.example'})
                self.assertIn('__Host-opsfusion_session=',r.headers['set-cookie']);self.assertIn('Secure',r.headers['set-cookie'])
                self.assertEqual(c.get('/access/verify').status_code,204)

    def test_invalid_public_modes_fail_closed(self):
        for mode,origin in [('local','http://remote.example'),('https','http://ops.example'),('other','https://ops.example'),('https','https://user:password@ops.example'),('https','https://ops.example/path')]:
            with patch.dict(os.environ,{'OPSFUSION_ACCESS_MODE':mode,'OPSFUSION_PUBLIC_ORIGIN':origin}):
                with self.assertRaises(ValueError):settings()

    def test_access_is_disabled_for_existing_local_workflow(self):
        with patch.dict(os.environ,{'OPSFUSION_ACCESS_MODE':'off'}):
            with TestClient(create_app(project_root=self.root)) as c:self.assertEqual(c.get('/access/login').status_code,404)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name);self.root=self.base/'site';self.root.mkdir()
        self.a=Accounts(self.root/'runs/accounts.sqlite3');self.a.bootstrap('fixture-admin',PW);self.token=self.a.login('fixture-admin',PW)
        self.h=History(self.root/'runs/history.sqlite3')
        self.sid=self.h.start(ZONE['camera_id'],ZONE['asset_id'],'webcam',capture_settings(ZONE))
        self.image=self.root/'runs/live'/self.sid/'evidence/camera.jpg';self.image.parent.mkdir(parents=True);self.image.write_bytes(b'fixture-jpeg')
        self.e=Event('portable-camera',datetime.now(timezone.utc).isoformat(),ZONE['asset_id'],'video','zone_entry',ZONE['camera_id'],self.image.as_uri(),'Fixture snapshot',1.,True)
        self.h.ingest(self.sid,[self.e]);self.h.finish(self.sid)
        (self.root/'models').mkdir();(self.root/'models/model.joblib').write_bytes(b'trusted-fixture-model')
        self.output=self.base/'backups'

    def backup(self):return backup(self.root,self.output,offline=True)

    def test_backup_restore_preserves_account_history_models_and_evidence(self):
        b=self.backup();target=self.base/'restored';result=restore(b['archive'],target,offline=True)
        a=Accounts(target/'runs/accounts.sqlite3');t=a.login('fixture-admin',PW);self.assertEqual(a.identity(t)['role'],'admin')
        with self.assertRaises(PermissionError):a.identity(self.token)
        h=History(target/'runs/history.sqlite3');self.assertEqual(h.incidents()[0]['first_report'],self.h.incidents()[0]['first_report'])
        self.assertEqual((target/'models/model.joblib').read_bytes(),b'trusted-fixture-model')
        self.assertEqual(evidence_path(target,self.e.evidence_ref),target/self.image.relative_to(self.root))
        self.assertTrue(result['auth_sessions_removed'])

    def test_backup_does_not_revoke_live_source_account(self):
        self.backup();self.assertEqual(self.a.identity(self.token)['username'],'fixture-admin')

    def test_requires_offline_attestation_and_rejects_active_streams(self):
        with self.assertRaises(ValueError):backup(self.root,self.output)
        self.h.start('second-camera',ZONE['asset_id'],'webcam',capture_settings(ZONE))
        with self.assertRaises(ValueError):self.backup()

    def test_service_lock_blocks_backup(self):
        with exclusive(self.root):
            with self.assertRaises(ValueError):self.backup()

    def test_restore_never_overwrites_existing_data(self):
        b=self.backup();target=self.base/'populated';target.mkdir();(target/'keep').write_text('preserved')
        with self.assertRaises(ValueError):restore(b['archive'],target,offline=True)
        self.assertEqual((target/'keep').read_text(),'preserved')

    def test_hash_mismatch_refuses_restore_without_partial_destination(self):
        b=self.backup();bad=self.base/'bad.zip'
        with zipfile.ZipFile(b['archive']) as src,zipfile.ZipFile(bad,'w') as dst:
            for n in src.namelist():dst.writestr(n,b'altered-model' if n=='models/model.joblib' else src.read(n))
        target=self.base/'failed'
        with self.assertRaises(ValueError):restore(bad,target,offline=True)
        self.assertFalse(target.exists())

    def test_traversal_duplicate_and_unlisted_members_rejected(self):
        b=self.backup()
        for addition in ['../outside','runs/../outside','runs//duplicate','unlisted']:
            bad=self.base/'bad.zip'
            with zipfile.ZipFile(b['archive']) as src,zipfile.ZipFile(bad,'w') as dst:
                for n in src.namelist():dst.writestr(n,src.read(n))
                dst.writestr(addition,b'unsafe')
            with self.assertRaises(ValueError):restore(bad,self.base/'failed',offline=True)
        self.assertFalse((self.base/'outside').exists())

    def test_symlink_source_rejected(self):
        link=self.root/'models/link';link.symlink_to(self.image)
        with self.assertRaises(ValueError):self.backup()

    def test_retention_preview_then_apply_only_managed_backups(self):
        for _ in range(3):self.backup()
        unrelated=self.output/'keep.zip';unrelated.write_bytes(b'unrelated')
        before=list(self.output.glob('opsfusion-backup-*.zip'));preview=retention(self.output,keep=1)
        self.assertEqual(len(preview['selected']),2);self.assertEqual(len(list(self.output.glob('opsfusion-backup-*.zip'))),3)
        applied=retention(self.output,keep=1,apply=True);self.assertEqual(len(applied['selected']),2)
        self.assertEqual(len(list(self.output.glob('opsfusion-backup-*.zip'))),1);self.assertTrue(unrelated.exists())

    def test_restored_path_allowlist_blocks_escape_and_remote_refs(self):
        b=self.backup();target=self.base/'restored';restore(b['archive'],target,offline=True)
        registry=target/'runs/restore-origins.json';data=json.loads(registry.read_text());data['origins'].append('file:///C:/Legacy%20OpsFusion');registry.write_text(json.dumps(data))
        windows='file:///C:/Legacy%20OpsFusion/'+self.image.relative_to(self.root).as_posix()
        self.assertEqual(evidence_path(target,windows),target/self.image.relative_to(self.root))
        for ref in ['https://attacker.example/image.jpg',self.root.as_uri()+'/runs/live/../../private.jpg',self.root.as_uri()+'/private.jpg','file://remote/runs/live/image.jpg']:
            self.assertIsNone(evidence_path(target,ref))


class DeploymentConfigTests(unittest.TestCase):
    def test_data_root_is_explicit_and_api_uses_it(self):
        with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,{'OPSFUSION_DATA_ROOT':td}):
            self.assertEqual(data_root(),Path(td).resolve())
            app=create_app();self.assertEqual(app.state.accounts_path,Path(td)/'runs/accounts.sqlite3')
        with patch.dict(os.environ,{'OPSFUSION_DATA_ROOT':'relative'}):
            with self.assertRaises(ValueError):data_root()

    def test_supervisor_commands_keep_local_binding_and_xsrf(self):
        with patch.dict(os.environ,{'OPSFUSION_ACCESS_MODE':'off'}):
            api,ui=commands(8000,8504,'127.0.0.1');self.assertIn('127.0.0.1',api);self.assertIn('--server.enableXsrfProtection',ui)
            with self.assertRaises(ValueError):commands(8000,8504,'0.0.0.0')

    def test_upgrade_step13_to_step15_preserves_accounts(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);old=root/'old';new=root/'new'
            for p,v in [(old,'0.13.0'),(new,'0.15.0')]:
                (p/'opsfusion').mkdir(parents=True);(p/'opsfusion/__init__.py').write_text("__version__ = '"+v+"'");(p/'dashboard.py').write_text(v)
            (old/'runs').mkdir();(old/'runs/accounts.sqlite3').write_bytes(b'preserved')
            self.assertEqual(upgrade(old,new)['to_version'],'0.15.0');self.assertEqual((old/'runs/accounts.sqlite3').read_bytes(),b'preserved')
            self.assertTrue(upgrade(old,new)['already_current'])
            with exclusive(old):
                with self.assertRaises(ValueError):upgrade(old,new)


if __name__=='__main__':unittest.main()
