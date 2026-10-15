from email import policy
from email.parser import BytesParser
import json
import os
from pathlib import Path
import shutil
import smtplib
import socketserver
import ssl
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from opsfusion.accounts import Accounts
from opsfusion.email_delivery import EmailDeliveryError, build_message, email_settings, email_target_hash, send_email
from opsfusion.history import History
from opsfusion.live import settings
from opsfusion.notify import Outbox
from opsfusion.upgrade import upgrade
from test_live_history import ZONE, event
from smtp_certificate_fixture import CERT, KEY

PW='Fixture password 123!'
CONFIG={'host':'smtp.example.invalid','port':587,'security':'starttls','username':'fixture@example.invalid',
        'from':'fixture@example.invalid','to':'inbox@example.invalid','password_env':'OPSFUSION_SMTP_PASSWORD'}


class EmailTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.tick=1000.;self.a=Accounts(self.root/'accounts.sqlite3',clock=lambda:self.tick)
        self.a.bootstrap('fixture-admin',PW);self.token=self.a.login('fixture-admin',PW)
        self.h=History(self.root/'history.sqlite3')
        self.sid=self.h.start('camera-1','zone-1','webcam',settings(ZONE))
        self.o=Outbox(self.h,self.a,clock=lambda:self.tick)
        self.config=dict(CONFIG)
        self.env=patch.dict(os.environ,{'OPSFUSION_EMAIL_SETTINGS':json.dumps(self.config),'OPSFUSION_SMTP_PASSWORD':'Fixture SMTP password!'});
        self.env.start();self.addCleanup(self.env.stop)

    def configure(self,accept=False):
        self.o.configure(self.token,'EmailInbox','OPSFUSION_EMAIL_SETTINGS',True,accept,'email')

    def new_notice(self,synthetic=False,identifier='e1',seconds=0):
        self.h.ingest(self.sid,[event(identifier,seconds,synthetic=synthetic)])

    def rows(self):return [r for r in self.o.rows(self.token) if r['channel']=='email']

    def queued(self):
        self.configure();self.new_notice();self.o.sync(self.token)
        return self.rows()[0]

    def test_valid_settings_and_ssl_default(self):
        self.assertEqual(email_settings('OPSFUSION_EMAIL_SETTINGS'),CONFIG)
        config=dict(CONFIG);config['security']='ssl';config.pop('port')
        with patch.dict(os.environ,{'OPSFUSION_EMAIL_SETTINGS':json.dumps(config)}):
            self.assertEqual(email_settings('OPSFUSION_EMAIL_SETTINGS')['port'],465)

    def test_invalid_config_redacts_addresses_and_credentials(self):
        for update in [{'security':'none'},{'port':True},{'port':70000},{'host':'https://smtp.invalid'},
                       {'host':'smtp.invalid\r\nsecret'},{'to':'one@example.invalid,two@example.invalid'},
                       {'to':'One Person <one@example.invalid>'},{'from':'one@example.invalid\r\nBcc: hidden'},
                       {'to':'@example.invalid'},{'to':'one@example.invalid@other.invalid'},{'to':'one@example..invalid'},
                       {'username':'fixture\nsecret'},{'password':'accidental-secret'},
                       {'password_env':'UNSAFE_ENV'},{'password_env':'OPSFUSION_EMAIL_SETTINGS'}]:
            with self.subTest(update=update),patch.dict(os.environ,{'OPSFUSION_EMAIL_SETTINGS':json.dumps({**CONFIG,**update})}):
                with self.assertRaises(ValueError) as exc:email_settings('OPSFUSION_EMAIL_SETTINGS')
                self.assertNotIn('accidental-secret',str(exc.exception));self.assertNotIn('@example.invalid',str(exc.exception))
        for raw in ['not json','[]','null','{}','x'*8193]:
            with patch.dict(os.environ,{'OPSFUSION_EMAIL_SETTINGS':raw}),self.assertRaises(ValueError):email_settings('OPSFUSION_EMAIL_SETTINGS')

    def test_missing_password_prevents_enabling(self):
        with patch.dict(os.environ,{'OPSFUSION_SMTP_PASSWORD':''}),self.assertRaises(ValueError):self.configure()
        self.assertFalse(any(d['name']=='EmailInbox' for d in self.o.destinations(self.token)))

    def test_test_email_without_incidents_and_dedup_pending_probe(self):
        self.configure();identifier=self.o.queue_test_email(self.token,'EmailInbox')
        self.assertEqual(self.o.queue_test_email(self.token,'EmailInbox'),identifier)
        self.assertEqual(self.h.incidents(),[]);self.assertEqual(self.h.notices(),[])
        self.assertEqual(len(self.rows()),1);row=self.rows()[0]
        self.assertLess(row['notice_id'],0);self.assertEqual(row['payload']['kind'],'test')
        self.assertEqual(row['payload']['requested_by'],'fixture-admin')
        self.assertEqual(self.o.sync(self.token),0)
        self.o.dispatch_one(self.token,email_transport=lambda *a:250)
        self.assertNotEqual(self.o.queue_test_email(self.token,'EmailInbox'),identifier)

    def test_test_email_requires_enabled_email_destination(self):
        for name in ['missing','Preview']:
            with self.assertRaises(ValueError):self.o.queue_test_email(self.token,name)
        self.configure();self.o.configure(self.token,'EmailInbox','OPSFUSION_EMAIL_SETTINGS',False,False,'email')
        with self.assertRaises(ValueError):self.o.queue_test_email(self.token,'EmailInbox')

    def test_existing_previews_do_not_send_on_enable(self):
        self.new_notice();self.o.sync(self.token);self.configure();self.o.sync(self.token)
        self.assertEqual(self.rows(),[])
        self.new_notice(identifier='e2',seconds=21);self.o.sync(self.token)
        self.assertEqual(len(self.rows()),1)

    def test_synthetic_blocked_but_explicit_probe_allowed(self):
        self.configure();self.new_notice(synthetic=True);self.o.sync(self.token)
        self.assertEqual(self.rows()[0]['state'],'suppressed')
        self.assertEqual(self.rows()[0]['error_code'],'synthetic_source_blocked')
        probe=self.o.queue_test_email(self.token,'EmailInbox')
        self.o.dispatch_one(self.token,email_transport=lambda *a:250)
        self.assertEqual(next(r for r in self.rows() if r['id']==probe)['state'],'delivered')

    def test_synthetic_opt_in_marks_demo_in_message(self):
        self.configure(accept=True);self.new_notice(synthetic=True);self.o.sync(self.token)
        row=self.rows()[0];message=build_message(CONFIG,row['payload'],row['id'])
        self.assertIn('DEMO',message['Subject']);self.assertIn('synthetic/demo',message.get_content())

    def test_email_dispatch_selects_smtp_never_webhook(self):
        self.queued();seen=[]
        with patch('opsfusion.notify.send_webhook',side_effect=AssertionError('Must use SMTP')):
            self.o.dispatch_one(self.token,email_transport=lambda config,payload,key:seen.append((config,payload,key)) or 250)
        self.assertEqual(self.rows()[0]['state'],'delivered');self.assertEqual(seen[0][0]['to'],CONFIG['to'])
        self.assertEqual(self.rows()[0]['status_code'],250)

    def test_smtp_temporary_rejection_retries_four_with_same_message_id(self):
        self.queued();seen=[]
        def fail(config,payload,key):
            seen.append(build_message(config,payload,key)['Message-ID'])
            raise EmailDeliveryError('smtp_rejected',True,451)
        for attempt in range(1,5):
            self.assertTrue(self.o.dispatch_one(self.token,email_transport=fail))
            row=self.rows()[0];self.assertEqual(row['attempts'],attempt)
            if attempt<4:
                self.assertEqual(row['state'],'pending');self.assertFalse(self.o.dispatch_one(self.token,email_transport=fail))
                self.tick=row['next_at']
        self.assertEqual(self.rows()[0]['state'],'failed');self.assertEqual(len(set(seen)),1)
        self.assertEqual(len(self.o.export(self.token)['attempts']),4)

    def test_smtp_5xx_and_auth_failure_are_permanent(self):
        self.queued()
        self.o.dispatch_one(self.token,email_transport=lambda *a:550)
        self.assertEqual(self.rows()[0]['state'],'failed');self.assertEqual(self.rows()[0]['error_code'],'smtp_rejected')
        self.o.queue_test_email(self.token,'EmailInbox')
        def fail(*a):raise EmailDeliveryError('smtp_auth_failed',False,535)
        self.o.dispatch_one(self.token,email_transport=fail)
        self.assertTrue(any(r['error_code']=='smtp_auth_failed' and r['state']=='failed' for r in self.rows()))

    def test_route_change_blocks_but_password_rotation_can_retry(self):
        row=self.queued()
        with patch.dict(os.environ,{'OPSFUSION_SMTP_PASSWORD':'Rotated fixture password!'}):
            self.o.dispatch_one(self.token,email_transport=lambda *a:250)
        self.assertEqual(self.rows()[0]['state'],'delivered')
        self.o.queue_test_email(self.token,'EmailInbox')
        with patch.dict(os.environ,{'OPSFUSION_EMAIL_SETTINGS':json.dumps({**CONFIG,'to':'changed@example.invalid'})}):
            self.o.dispatch_one(self.token,email_transport=lambda *a:self.fail('Recipient changed; must not send'))
        self.assertTrue(any(r['error_code']=='endpoint_changed' for r in self.rows()))

    def test_reconfigure_cancels_queued_email(self):
        self.queued();self.configure()
        self.assertEqual(self.rows()[0]['state'],'cancelled')
        self.assertFalse(self.o.dispatch_one(self.token,email_transport=lambda *a:self.fail('Cancelled')))

    def test_password_config_and_raw_paths_excluded_from_database_and_export(self):
        self.queued();self.o.queue_test_email(self.token,'EmailInbox')
        self.assertNotIn('file://',json.dumps(self.o.export(self.token)))
        with self.h.connect() as db:
            all_rows=' '.join(str(tuple(r)) for t in ('destinations','deliveries','notification_audit') for r in db.execute('SELECT * FROM '+t))
        for secret in ['Fixture SMTP password!','inbox@example.invalid','smtp.example.invalid','fixture@example.invalid']:
            self.assertNotIn(secret,all_rows);self.assertNotIn(secret,json.dumps(self.o.export(self.token)))

    def test_viewer_operator_and_revoked_admin_cannot_queue_test(self):
        self.configure()
        for role in ('viewer','operator'):
            self.a.add_user(self.token,'fixture-'+role,PW,role);token=self.a.login('fixture-'+role,PW)
            with self.assertRaises(PermissionError):self.o.queue_test_email(token,'EmailInbox')
            with self.assertRaises(PermissionError):self.o.configure(token,'OtherEmail','OPSFUSION_EMAIL_SETTINGS',True,False,'email')
        self.a.logout(self.token)
        with self.assertRaises(PermissionError):self.o.queue_test_email(self.token,'EmailInbox')

    def test_message_is_stable_safe_and_evidence_only(self):
        row=self.queued();payload=row['payload'];payload['incident']['asset_id']='unsafe\r\nBcc: hidden'
        message=build_message(CONFIG,payload,row['id'])
        self.assertNotIn('\n',message['Subject']);self.assertIsNone(message['Bcc'])
        self.assertEqual(message['Message-ID'],build_message(CONFIG,payload,row['id'])['Message-ID'])
        self.assertIn('No machine-control action was executed.',message.get_content())
        self.assertIn('e1',message.get_content());self.assertNotIn('file://',message.get_content())
        self.assertNotIn('Fixture SMTP password!',message.as_string());self.assertEqual(message['To'],CONFIG['to'])

    def test_starttls_precedes_login_verified_context_and_one_recipient(self):
        row=self.queued();client=MagicMock();client.send_message.return_value={}
        with patch('opsfusion.email_delivery.smtplib.SMTP',return_value=client) as smtp:
            self.assertEqual(send_email(CONFIG,row['payload'],row['id']),250)
        calls=[c[0] for c in client.method_calls]
        self.assertEqual(calls[:5],['ehlo','starttls','ehlo','login','send_message'])
        context=client.starttls.call_args.kwargs['context']
        self.assertTrue(context.check_hostname);self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
        self.assertEqual(client.send_message.call_args.kwargs['to_addrs'],[CONFIG['to']])
        self.assertEqual(smtp.call_args.kwargs['timeout'],5)

    def test_tls_failure_never_authenticates_or_sends(self):
        row=self.queued();client=MagicMock();client.starttls.side_effect=ssl.SSLCertVerificationError('private secret')
        with patch('opsfusion.email_delivery.smtplib.SMTP',return_value=client):
            with self.assertRaises(EmailDeliveryError) as exc:send_email(CONFIG,row['payload'],row['id'])
        self.assertEqual(exc.exception.code,'smtp_tls_failed');self.assertFalse(exc.exception.retryable)
        client.login.assert_not_called();client.send_message.assert_not_called();self.assertNotIn('private secret',str(exc.exception))

    def test_ssl_uses_verified_context(self):
        row=self.queued();client=MagicMock();client.send_message.return_value={}
        with patch('opsfusion.email_delivery.smtplib.SMTP_SSL',return_value=client) as smtp:
            self.assertEqual(send_email({**CONFIG,'security':'ssl','port':465},row['payload'],row['id']),250)
        self.assertTrue(smtp.call_args.kwargs['context'].check_hostname);client.starttls.assert_not_called()

    def test_recipient_and_data_errors_redacted_classified(self):
        row=self.queued()
        for error,code,retry in [(smtplib.SMTPAuthenticationError(535,b'private credential'), 'smtp_auth_failed',False),
                                 (smtplib.SMTPRecipientsRefused({CONFIG['to']:(450,b'private recipient')}),'smtp_recipient_rejected',True),
                                 (smtplib.SMTPDataError(554,b'private policy'),'smtp_rejected',False),
                                 (smtplib.SMTPServerDisconnected('private reply'),'smtp_delivery_uncertain',True),
                                 (smtplib.SMTPNotSupportedError('private TLS'),'smtp_tls_or_auth_unsupported',False)]:
            client=MagicMock();client.send_message.side_effect=error
            with patch('opsfusion.email_delivery.smtplib.SMTP',return_value=client),self.assertRaises(EmailDeliveryError) as exc:
                send_email(CONFIG,row['payload'],row['id'])
            self.assertEqual(exc.exception.code,code);self.assertEqual(exc.exception.retryable,retry)
            self.assertNotIn('private',str(exc.exception))

    def test_quit_failure_after_acceptance_does_not_resend(self):
        row=self.queued();client=MagicMock();client.send_message.return_value={}
        client.quit.side_effect=smtplib.SMTPServerDisconnected('fixture quit disconnect')
        with patch('opsfusion.email_delivery.smtplib.SMTP',return_value=client):
            self.assertEqual(send_email(CONFIG,row['payload'],row['id']),250)
        client.close.assert_called_once()

    def test_legacy_step10_queue_migration_retains_webhooks_and_payloads(self):
        # Simulate the exact pre-email table definitions; ALTER migration is additive.
        other=History(self.root/'legacy.sqlite3')
        with other.connect() as db:
            db.executescript("""CREATE TABLE destinations(name TEXT PRIMARY KEY,env_name TEXT,enabled INTEGER NOT NULL,accept_synthetic INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 1,cursor INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE deliveries(id TEXT PRIMARY KEY,destination TEXT NOT NULL,destination_revision INTEGER NOT NULL,notice_id INTEGER NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_at REAL NOT NULL DEFAULT 0,target_hash TEXT,lease_token TEXT,lease_until REAL,status_code INTEGER,error_code TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL,UNIQUE(destination,destination_revision,notice_id));
                INSERT INTO destinations VALUES('OldHook','OPSFUSION_WEBHOOK_URL',0,0,2,10);
                INSERT INTO deliveries(id,destination,destination_revision,notice_id,payload,state,created_at,updated_at) VALUES('old-record','OldHook',2,10,'{}','preview',1,1);""")
        box=Outbox(other,self.a,clock=lambda:self.tick);box=Outbox(other,self.a,clock=lambda:self.tick)
        row=box.rows(self.token)[0];self.assertEqual(row['channel'],'webhook');self.assertEqual(row['payload'],{})
        self.assertEqual(next(d for d in box.destinations(self.token) if d['name']=='OldHook')['cursor'],10)

    def test_in_place_upgrade_preserves_account_login_queue_and_paths(self):
        row=self.queued();self.h.finish(self.sid)
        old=self.root/'old';new=self.root/'new'
        for folder,version in [(old,'0.10.0'),(new,'0.11.0')]:
            (folder/'opsfusion').mkdir(parents=True);(folder/'opsfusion/__init__.py').write_text("__version__ = '"+version+"'")
            (folder/'dashboard.py').write_text(version)
        (old/'runs').mkdir();shutil.copy(self.h.path,old/'runs/history.sqlite3');shutil.copy(self.a.path,old/'runs/accounts.sqlite3')
        profile=(old/'runs/accounts.sqlite3').read_bytes();history=(old/'runs/history.sqlite3').read_bytes()
        result=upgrade(old,new)
        self.assertEqual(result['to_version'],'0.11.0');self.assertEqual(result['from_version'],'0.10.0')
        self.assertEqual((old/'runs/accounts.sqlite3').read_bytes(),profile)
        self.assertEqual((old/'runs/history.sqlite3').read_bytes(),history)
        self.assertEqual(Accounts(old/'runs/accounts.sqlite3',clock=lambda:self.tick).identity(self.token)['username'],'fixture-admin')


class SMTPFixture:
    """An actual local TLS SMTP listener; it never forwards messages elsewhere."""
    def __init__(self,root,mode='starttls'):
        self.received=[];self.auth_tls=[];self.mode=mode
        cert=root/'localhost.pem';key=root/'localhost.key';cert.write_text(CERT);key.write_text(KEY)
        self.client_context=ssl.create_default_context(cafile=str(cert))
        server_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server_context.load_cert_chain(cert,key)
        fixture=self
        class Handler(socketserver.StreamRequestHandler):
            def setup(self):
                if fixture.mode=='ssl':self.request=server_context.wrap_socket(self.request,server_side=True)
                super().setup()
            def handle(self):
                secured=fixture.mode=='ssl'
                def reply(text):self.wfile.write((text+'\r\n').encode());self.wfile.flush()
                reply('220 localhost OpsFusion test SMTP')
                while True:
                    raw=self.rfile.readline()
                    if not raw:break
                    command=raw.decode().strip().split(' ',1)[0].upper()
                    if command=='EHLO':
                        reply('250-localhost')
                        if not secured:reply('250-STARTTLS')
                        reply('250 AUTH PLAIN LOGIN')
                    elif command=='STARTTLS':
                        reply('220 Start TLS');self.rfile.close();self.wfile.close()
                        try:self.request=server_context.wrap_socket(self.request,server_side=True)
                        except ssl.SSLError:return
                        self.rfile=self.request.makefile('rb');self.wfile=self.request.makefile('wb');secured=True
                    elif command=='AUTH':fixture.auth_tls.append(secured);reply('235 Authenticated fixture')
                    elif command in ('MAIL','RCPT','RSET'):reply('250 OK')
                    elif command=='DATA':
                        reply('354 Send data');data=[]
                        while True:
                            line=self.rfile.readline()
                            if not line or line==b'.\r\n':break
                            data.append(line[1:] if line.startswith(b'..') else line)
                        fixture.received.append(BytesParser(policy=policy.default).parsebytes(b''.join(data)));reply('250 Accepted fixture')
                    elif command=='QUIT':reply('221 Bye');return
                    else:reply('500 Unsupported fixture command')
            def finish(self):
                try:super().finish()
                finally:self.request.close()
        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address=True;daemon_threads=True
            def handle_error(self,*args):pass
        self.server=Server(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    @property
    def config(self):return {**CONFIG,'host':'127.0.0.1','port':self.server.server_address[1],'security':self.mode}
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)


class ActualSMTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.payload={'kind':'test','requested_at':1000.};self.identifier='a'*64
        self.env=patch.dict(os.environ,{'OPSFUSION_SMTP_PASSWORD':'Public fixture-only password'});self.env.start();self.addCleanup(self.env.stop)

    def test_actual_starttls_smtp_acceptance(self):
        fixture=SMTPFixture(self.root);self.addCleanup(fixture.close)
        with patch('opsfusion.email_delivery.ssl.create_default_context',return_value=fixture.client_context):
            self.assertEqual(send_email(fixture.config,self.payload,self.identifier),250)
        self.assertEqual(fixture.auth_tls,[True]);self.assertEqual(len(fixture.received),1)
        self.assertEqual(fixture.received[0]['Message-ID'],'<'+self.identifier+'@opsfusion.local>')
        self.assertIn('No incident was detected',fixture.received[0].get_content())

    def test_actual_implicit_tls_smtp_acceptance(self):
        fixture=SMTPFixture(self.root,'ssl');self.addCleanup(fixture.close)
        with patch('opsfusion.email_delivery.ssl.create_default_context',return_value=fixture.client_context):
            self.assertEqual(send_email(fixture.config,self.payload,self.identifier),250)
        self.assertEqual(fixture.auth_tls,[True]);self.assertEqual(len(fixture.received),1)

    def test_actual_untrusted_tls_certificate_rejected_before_auth(self):
        fixture=SMTPFixture(self.root);self.addCleanup(fixture.close)
        with self.assertRaises(EmailDeliveryError) as exc:send_email(fixture.config,self.payload,self.identifier)
        self.assertEqual(exc.exception.code,'smtp_tls_failed');self.assertEqual(fixture.auth_tls,[]);self.assertEqual(fixture.received,[])


class EmailDashboardTests(unittest.TestCase):
    def setUp(self):
        from ui_fixture import authenticated_app
        self.at=authenticated_app(self,Path(__file__).resolve().parents[1])
        self.env=patch.dict(os.environ,{'OPSFUSION_EMAIL_SETTINGS':json.dumps(CONFIG),'OPSFUSION_SMTP_PASSWORD':'Fixture password!'});self.env.start();self.addCleanup(self.env.stop)
        self.at.selectbox(key='source').select('Live monitoring & history').run()

    def test_email_configuration_and_probe_queue_from_ui_no_network(self):
        self.assertEqual(self.at.selectbox(key='notify_channel').value,'Email inbox')
        self.at.checkbox(key='notify_enabled').check()
        next(b for b in self.at.button if b.label=='Save destination settings').click().run()
        self.assertFalse(self.at.exception)
        with patch('opsfusion.notify.send_email',side_effect=AssertionError('UI must not send')):
            self.at.button(key='notify_test_email').click().run()
        self.assertFalse(self.at.exception)
        self.assertTrue(any('Test email queued:' in s.value for s in self.at.success))
        self.at.button(key='notify_refresh').click().run();self.assertFalse(self.at.exception)

    def test_missing_config_reports_helpful_error(self):
        with patch.dict(os.environ,{'OPSFUSION_EMAIL_SETTINGS':''}):
            self.at.checkbox(key='notify_enabled').check()
            next(b for b in self.at.button if b.label=='Save destination settings').click().run()
        self.assertFalse(self.at.exception)
        self.assertTrue(any('set_email_environment.ps1' in e.value for e in self.at.error))
