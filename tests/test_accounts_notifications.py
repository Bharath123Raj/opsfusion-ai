from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import shutil
import unittest
from unittest.mock import patch

from opsfusion.accounts import Accounts
from opsfusion.history import History
from opsfusion.notify import Outbox, target_url
from test_live_history import event, ZONE
from opsfusion.live import settings

PW='Fixture password 123!'


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.tick=1000.;self.a=Accounts(Path(self.temp.name)/'accounts.sqlite3',clock=lambda:self.tick)
        self.a.bootstrap('bharath',PW);self.token=self.a.login('bharath',PW)

    def test_no_default_password_bootstrap_once(self):
        with self.assertRaises(ValueError):self.a.bootstrap('other-admin',PW)
        with self.assertRaises(PermissionError):self.a.login('bharath','admin')
        self.assertEqual(self.a.identity(self.token),{'username':'bharath','role':'admin'})

    def test_credentials_and_bearer_not_stored_plaintext(self):
        with self.a.connect() as db:
            user=dict(db.execute('SELECT * FROM users').fetchone());session=dict(db.execute('SELECT * FROM auth_sessions').fetchone())
        self.assertNotEqual(user['password_hash'],PW)
        self.assertNotEqual(session['token_hash'],self.token)
        self.assertNotIn(PW,json.dumps(self.a.audit(self.token)))
        self.assertNotIn('password_hash',json.dumps(self.a.users(self.token)))

    def test_roles_and_server_side_denial(self):
        for role in ('operator','viewer'):
            self.a.add_user(self.token,role+'-user',PW,role)
            token=self.a.login(role+'-user',PW)
            self.a.require(token,'read')
            with self.assertRaises(PermissionError):self.a.require(token,'notify_admin')
            with self.assertRaises(PermissionError):self.a.require(token,'model_admin')
            with self.assertRaises(PermissionError):self.a.add_user(token,'unallowed-user',PW,'admin')
            if role=='viewer':
                for action in ('review','capture','analyse'):
                    with self.assertRaises(PermissionError):self.a.require(token,action)
            else:self.a.require(token,'review');self.a.require(token,'capture')

    def test_expiry_and_logout(self):
        short=self.a.login('bharath',PW,ttl=2);self.tick+=3
        with self.assertRaises(PermissionError):self.a.identity(short)
        self.a.logout(self.token)
        with self.assertRaises(PermissionError):self.a.identity(self.token)

    def test_disable_and_role_change_revoke_sessions(self):
        self.a.add_user(self.token,'review-user',PW,'operator');token=self.a.login('review-user',PW)
        self.a.set_user(self.token,'review-user','viewer')
        with self.assertRaises(PermissionError):self.a.identity(token)
        token=self.a.login('review-user',PW)
        self.a.set_user(self.token,'review-user','viewer',False)
        with self.assertRaises(PermissionError):self.a.identity(token)
        with self.assertRaises(PermissionError):self.a.login('review-user',PW)

    def test_last_admin_cannot_be_disabled(self):
        for role,enabled in [('viewer',True),('admin',False)]:
            with self.assertRaises(ValueError):self.a.set_user(self.token,'bharath',role,enabled)

    def test_throttle_survives_failed_transactions(self):
        for _ in range(5):
            with self.assertRaises(PermissionError):self.a.login('unknown-user',PW)
        with self.a.connect() as db:
            self.assertEqual(db.execute("SELECT failures FROM login_limits WHERE username='unknown-user'").fetchone()[0],5)
        for _ in range(5):
            with self.assertRaises(PermissionError):self.a.login('bharath','wrong-password')
        with self.assertRaises(PermissionError):self.a.login('bharath',PW)
        self.tick+=301;self.assertEqual(self.a.identity(self.a.login('bharath',PW))['username'],'bharath')

    def test_password_change_revokes_all_sessions(self):
        second=self.a.login('bharath',PW)
        self.a.change_password(self.token,PW,'Changed fixture password 456!')
        for token in (self.token,second):
            with self.assertRaises(PermissionError):self.a.identity(token)
        with self.assertRaises(PermissionError):self.a.login('bharath',PW)
        self.a.login('bharath','Changed fixture password 456!')

    def test_invalid_user_and_password_policy(self):
        for name,pw in [('a',PW),('someone@site',PW),('valid-user','short')]:
            with self.assertRaises(ValueError):self.a.add_user(self.token,name,pw,'operator')


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);root=Path(self.temp.name)
        self.tick=1000.;self.a=Accounts(root/'accounts.sqlite3',clock=lambda:self.tick)
        self.a.bootstrap('fixture-admin',PW);self.token=self.a.login('fixture-admin',PW)
        self.h=History(root/'history.sqlite3');self.sid=self.h.start('camera-1','zone-1','webcam',settings(ZONE))
        self.o=Outbox(self.h,self.a,clock=lambda:self.tick)
        self.env=patch.dict(os.environ,{'OPSFUSION_TEST_WEBHOOK':'https://example.invalid/fixture-secret'})
        self.env.start();self.addCleanup(self.env.stop)

    def configure(self,accept=False):self.o.configure(self.token,'TestHook','OPSFUSION_TEST_WEBHOOK',True,accept)
    def real_event(self,identifier='e1',seconds=0):self.h.ingest(self.sid,[event(identifier,seconds,synthetic=False)])
    def pending(self):return [r for r in self.o.rows(self.token) if r['destination']=='TestHook']

    def test_preview_only_and_dedup_no_network(self):
        self.real_event();self.assertEqual(self.o.sync(self.token),1)
        self.assertEqual(self.o.sync(self.token),0)
        self.assertEqual(self.o.rows(self.token)[0]['state'],'preview')
        with patch('opsfusion.notify.send_webhook',side_effect=AssertionError('Must not send')):
            self.assertFalse(self.o.dispatch_one(self.token))

    def test_idempotency_keys_are_scoped_to_installation(self):
        self.real_event();self.o.sync(self.token);first=self.o.rows(self.token)[0]['id']
        other=History(self.h.path.parent/'other-history.sqlite3')
        sid=other.start('camera-1','zone-1','webcam',settings(ZONE));other.ingest(sid,[event(synthetic=False)])
        second=Outbox(other,self.a,clock=lambda:self.tick);second.sync(self.token)
        self.assertNotEqual(first,second.rows(self.token)[0]['id'])

    def test_enable_new_notices_without_backlog(self):
        self.real_event();self.configure();self.o.sync(self.token)
        self.assertEqual(self.pending(),[])
        self.real_event('e2',21);self.o.sync(self.token)
        self.assertEqual(len(self.pending()),1)

    def test_synthetic_blocked_unless_explicit(self):
        self.configure();self.h.ingest(self.sid,[event()]);self.o.sync(self.token)
        self.assertEqual(self.pending()[0]['state'],'suppressed')
        self.assertEqual(self.pending()[0]['error_code'],'synthetic_source_blocked')
        self.configure(accept=True);self.h.ingest(self.sid,[event('e2',21)]);self.o.sync(self.token)
        self.assertTrue(any(r['state']=='pending' for r in self.pending()))

    def test_first_payload_never_acquires_future_evidence(self):
        self.real_event();self.real_event('e2',2);self.o.sync(self.token)
        self.assertEqual(self.o.rows(self.token)[0]['payload']['evidence_ids'],['e1'])

    def test_reopening_payload_frozen_at_notice(self):
        self.real_event();row=self.h.incidents()[0]
        self.h.review(row['incident_id'],'resolved','fixture-admin','',row['revision'],authenticated=True)
        self.real_event('e2',1);self.real_event('e3',2);self.o.sync(self.token)
        r=next(r for r in self.o.rows(self.token) if r['payload']['reason']=='new_evidence_after_review')
        self.assertEqual(r['payload']['evidence_ids'],['e1','e2'])

    def test_missing_legacy_snapshot_suppressed(self):
        self.real_event()
        with self.h.connect() as db:db.execute('UPDATE notices SET payload=NULL')
        self.o.sync(self.token);self.assertEqual(self.o.rows(self.token)[0]['error_code'],'legacy_snapshot_unavailable')

    def test_success_records_attempt_and_idempotency_key(self):
        self.configure();self.real_event();self.o.sync(self.token);seen=[]
        self.assertTrue(self.o.dispatch_one(self.token,lambda url,payload,key:seen.append(key) or 204))
        r=self.pending()[0];self.assertEqual(r['state'],'delivered');self.assertEqual(seen,[r['id']])
        self.assertFalse(self.o.dispatch_one(self.token))
        self.assertEqual(self.o.export(self.token)['attempts'][0]['status_code'],204)

    def test_retry_backoff_and_max_four(self):
        self.configure();self.real_event();self.o.sync(self.token);seen=[]
        for attempt in range(1,5):
            self.assertTrue(self.o.dispatch_one(self.token,lambda u,p,k:seen.append(k) or 503))
            r=self.pending()[0];self.assertEqual(r['attempts'],attempt)
            if attempt<4:
                self.assertEqual(r['state'],'pending');self.assertFalse(self.o.dispatch_one(self.token))
                self.tick=r['next_at']
        self.assertEqual(self.pending()[0]['state'],'failed');self.assertEqual(len(set(seen)),1)

    def test_429_retries_and_400_redirect_do_not(self):
        self.configure();self.real_event();self.o.sync(self.token)
        self.o.dispatch_one(self.token,lambda *a:429);self.assertEqual(self.pending()[0]['state'],'pending')
        self.tick=self.pending()[0]['next_at'];self.o.dispatch_one(self.token,lambda *a:302)
        self.assertEqual(self.pending()[0]['state'],'failed');self.assertEqual(self.pending()[0]['error_code'],'redirect_rejected')

    def test_transport_exception_redacts_and_retries(self):
        self.configure();self.real_event();self.o.sync(self.token)
        def fail(*args):raise RuntimeError('url contains fixture-secret')
        self.o.dispatch_one(self.token,fail)
        self.assertEqual(self.pending()[0]['state'],'pending')
        self.assertNotIn('fixture-secret',json.dumps(self.o.export(self.token)))

    def test_endpoint_change_and_pausing_prevent_delivery(self):
        self.configure();self.real_event();self.o.sync(self.token)
        with patch.dict(os.environ,{'OPSFUSION_TEST_WEBHOOK':'https://example.invalid/changed'}):
            self.o.dispatch_one(self.token,lambda *a:self.fail('Endpoint changed; must not send'))
        self.assertEqual(self.pending()[0]['error_code'],'endpoint_changed')
        self.real_event('e2',21);self.o.sync(self.token)
        self.o.configure(self.token,'TestHook','OPSFUSION_TEST_WEBHOOK',False)
        self.assertFalse(self.o.dispatch_one(self.token))
        self.assertTrue(any(r['state']=='cancelled' for r in self.pending()))

    def test_lease_recovery_is_uncertain_at_least_once(self):
        self.configure();self.real_event();self.o.sync(self.token)
        claim=self.o._claim(self.token);self.assertIsNotNone(claim)
        self.assertIsNone(self.o._claim(self.token));self.tick+=61
        next_claim=self.o._claim(self.token)
        self.assertEqual(next_claim['id'],claim['id']);self.assertEqual(next_claim['attempts'],2)

    def test_non_admin_and_revoked_token_cannot_dispatch(self):
        self.configure();self.real_event();self.o.sync(self.token)
        self.a.add_user(self.token,'viewer-user',PW,'viewer');viewer=self.a.login('viewer-user',PW)
        for action in [lambda:self.o.configure(viewer,'OtherHook','OPSFUSION_TEST_WEBHOOK',True),lambda:self.o.sync(viewer),lambda:self.o.dispatch_one(viewer)]:
            with self.assertRaises(PermissionError):action()
        self.a.logout(self.token)
        with self.assertRaises(PermissionError):self.o.dispatch_one(self.token)

    def test_persistence_and_no_secret_exports(self):
        self.configure();self.real_event();self.o.sync(self.token)
        again=Outbox(History(self.h.path),Accounts(self.a.path,clock=lambda:self.tick),clock=lambda:self.tick)
        self.assertEqual(again.rows(self.token),self.o.rows(self.token))
        self.assertNotIn('fixture-secret',json.dumps(again.export(self.token)))
        self.assertNotIn('file://',json.dumps(again.rows(self.token)[0]['payload']))

    def test_url_validation_https_no_userinfo_redirect_targets(self):
        for url in ['http://127.0.0.1/hook','https://user:password@example.invalid/hook','file:///private','https://host/hook#token']:
            with patch.dict(os.environ,{'OPSFUSION_TEST_WEBHOOK':url}),self.assertRaises(ValueError):target_url('OPSFUSION_TEST_WEBHOOK')

    def test_actual_loopback_http_transport(self):
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append((self.headers['Idempotency-Key'],payload))
                self.send_response(204);self.end_headers()
            def log_message(self,*args):pass
        server=HTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with patch.dict(os.environ,{'OPSFUSION_TEST_WEBHOOK':f'http://127.0.0.1:{server.server_port}/fixture'}):
                test_box=Outbox(self.h,self.a,clock=lambda:self.tick,allow_loopback_http=True)
                test_box.configure(self.token,'TestHook','OPSFUSION_TEST_WEBHOOK',True)
                self.real_event();test_box.sync(self.token);test_box.dispatch_one(self.token)
                delivered=next(r for r in test_box.rows(self.token) if r['destination']=='TestHook')
                self.assertEqual(delivered['state'],'delivered')
                self.assertEqual(received[0][0],received[0][1]['delivery_id'])
        finally:server.shutdown();server.server_close();thread.join(timeout=2)


class AuthApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from opsfusion.api import create_app
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.a=Accounts(Path(self.temp.name)/'accounts.sqlite3');self.a.bootstrap('fixture-admin',PW)
        self.client=TestClient(create_app(self.a.path));self.addCleanup(self.client.close)

    def test_public_health_and_protected_review(self):
        self.assertEqual(self.client.get('/health').status_code,200)
        self.assertEqual(self.client.get('/v1/contract').status_code,401)
        self.assertEqual(self.client.post('/v1/replay',json={'events':[]}).status_code,401)

    def test_auth_validation_does_not_echo_password(self):
        secret='fixture-sensitive-'*30
        response=self.client.post('/auth/login',json={'username':'fixture-admin','password':secret})
        self.assertEqual(response.status_code,422);self.assertNotIn(secret,response.text)

    def test_login_bearer_logout_revocation(self):
        response=self.client.post('/auth/login',json={'username':'fixture-admin','password':PW})
        self.assertEqual(response.status_code,200);self.assertEqual(response.headers['cache-control'],'no-store')
        token=response.json()['access_token'];headers={'Authorization':'Bearer '+token}
        self.assertEqual(self.client.post('/v1/replay',json={'events':[]},headers=headers).status_code,200)
        self.assertEqual(self.client.post('/auth/logout',headers=headers).status_code,200)
        self.assertEqual(self.client.get('/v1/contract',headers=headers).status_code,401)


    def test_password_and_forged_bearer_rejected(self):
        self.assertEqual(self.client.post('/auth/login',json={'username':'fixture-admin','password':'wrong'}).status_code,401)
        self.assertEqual(self.client.get('/v1/contract',headers={'Authorization':'Bearer fabricated-token-123456789'}).status_code,401)

    def test_role_change_revokes_api_session(self):
        admin=self.a.login('fixture-admin',PW);self.a.add_user(admin,'viewer-user',PW,'viewer')
        token=self.a.login('viewer-user',PW);headers={'Authorization':'Bearer '+token}
        self.assertEqual(self.client.get('/v1/contract',headers=headers).status_code,200)
        self.a.set_user(admin,'viewer-user','viewer',False)
        self.assertEqual(self.client.get('/v1/contract',headers=headers).status_code,401)


class AuthDashboardTests(unittest.TestCase):
    def setUp(self):
        from streamlit.testing.v1 import AppTest
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        source=Path(__file__).resolve().parents[1]
        shutil.copy(source/'dashboard.py',self.root/'dashboard.py');shutil.copytree(source/'data',self.root/'data')
        self.a=Accounts(self.root/'runs/accounts.sqlite3')
        self.at=AppTest.from_file(str(self.root/'dashboard.py'),default_timeout=30)

    def test_no_anonymous_history_or_raw_inputs(self):
        self.at.run();self.assertFalse(self.at.exception)
        self.assertTrue(any('Create the first admin' in x.value for x in self.at.info))
        self.assertEqual(len(self.at.selectbox),0)

    def test_actual_form_signin_and_logout(self):
        self.a.bootstrap('bharath',PW);self.at.run()
        self.at.text_input(key='signin_username').set_value('bharath')
        self.at.text_input(key='signin_password').set_value(PW)
        next(b for b in self.at.button if b.label=='Sign in').click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.selectbox(key='source').value,'Real recordings')
        self.assertFalse(any(x.label=='Password' for x in self.at.text_input))
        self.at.button(key='signout').click().run()
        self.assertFalse(self.at.exception);self.assertFalse(self.at.selectbox)
        self.assertTrue(any(x.label=='Password' for x in self.at.text_input))

    def test_password_change_form_revokes_and_signs_out(self):
        self.a.bootstrap('bharath',PW);token=self.a.login('bharath',PW)
        self.at.session_state.security_token=token;self.at.run()
        next(x for x in self.at.text_input if x.label=='Current password').set_value(PW)
        next(x for x in self.at.text_input if x.label=='New password (12+ characters)').set_value('Changed browser password 456!')
        next(x for x in self.at.text_input if x.label=='Confirm new password').set_value('Changed browser password 456!')
        next(b for b in self.at.button if b.label=='Change password and sign out').click().run()
        self.assertFalse(self.at.exception);self.assertFalse(self.at.selectbox)
        with self.assertRaises(PermissionError):self.a.identity(token)
        self.a.login('bharath','Changed browser password 456!')

    def test_viewer_actions_disabled_and_actor_not_editable(self):
        self.a.bootstrap('admin-user',PW);admin=self.a.login('admin-user',PW)
        self.a.add_user(admin,'viewer-user',PW,'viewer')
        h=History(self.root/'runs/history.sqlite3');sid=h.start('camera-1','zone-1','webcam',settings(ZONE));h.ingest(sid,[event()]);h.finish(sid)
        self.at.session_state.security_token=self.a.login('viewer-user',PW);self.at.run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.selectbox(key='source').options,['Live monitoring & history'])
        self.assertTrue(self.at.button(key='live_start').disabled)
        self.assertTrue(self.at.button(key='notify_sync').disabled)
        self.assertTrue(next(b for b in self.at.button if b.label=='Save review').disabled)
        self.assertFalse(any(x.label=='Operator label' for x in self.at.text_input))

    def test_revoked_user_returns_to_login(self):
        self.a.bootstrap('admin-user',PW);admin=self.a.login('admin-user',PW)
        self.a.add_user(admin,'operator-user',PW,'operator')
        self.at.session_state.security_token=self.a.login('operator-user',PW);self.at.run()
        self.a.set_user(admin,'operator-user','viewer');self.at.run()
        self.assertFalse(self.at.exception);self.assertFalse(self.at.selectbox)
        self.assertTrue(any(x.label=='Password' for x in self.at.text_input))

    def test_operator_cannot_choose_local_deserialized_models(self):
        self.a.bootstrap('admin-user',PW);admin=self.a.login('admin-user',PW)
        self.a.add_user(admin,'operator-user',PW,'operator')
        self.at.session_state.security_token=self.a.login('operator-user',PW);self.at.run()
        self.at.checkbox(key='raw_video_on').uncheck()
        self.at.checkbox(key='raw_sensor_on').check().run()
        self.assertEqual(self.at.radio(key='raw_sensor_method').options,['Train from normal files'])
        self.at.checkbox(key='raw_audio_on').check().run()
        self.assertEqual(self.at.radio(key='raw_audio_method').options,['Train from normal files'])
        self.assertFalse(any(x.label.startswith('Trusted ') for x in self.at.text_input))

    def test_preview_queue_from_ui_no_destination(self):
        self.a.bootstrap('admin-user',PW);token=self.a.login('admin-user',PW)
        h=History(self.root/'runs/history.sqlite3');sid=h.start('camera-1','zone-1','webcam',settings(ZONE));h.ingest(sid,[event()]);h.finish(sid)
        self.at.session_state.security_token=token;self.at.run()
        self.at.selectbox(key='source').select('Live monitoring & history').run()
        self.at.button(key='notify_sync').click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(Outbox(h,self.a).rows(token)[0]['state'],'preview')

