from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from opsfusion.accounts import Accounts
from opsfusion.api import create_app
from opsfusion.sensor import score_rows
from opsfusion.stream_store import StreamStore,StreamConflict,MAX_INPUTS,MAX_EVENTS
from opsfusion.stream import local_url
from opsfusion.upgrade import upgrade

ROOT=Path(__file__).resolve().parents[1]
PW='Stream fixture password 123!'


class StreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline_temp=tempfile.TemporaryDirectory();root=Path(cls.baseline_temp.name)
        a=Accounts(root/'runs/accounts.sqlite3');a.bootstrap('fixture-admin',PW);token=a.login('fixture-admin',PW)
        s=StreamStore(root);cls.registered=s.register(token,ROOT/'data/sensor-demo/train.csv',ROOT/'data/sensor-demo/validation.csv')
        cls.registered_root=root
    @classmethod
    def tearDownClass(cls):cls.baseline_temp.cleanup()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        shutil.copytree(self.registered_root/'runs',self.root/'runs')
        self.tick=datetime(2026,10,14,10,0,tzinfo=timezone.utc).timestamp()
        self.s=StreamStore(self.root,clock=lambda:self.tick)
        self.a=self.s.accounts;self.token=self.a.login('fixture-admin',PW)
        self.bid=self.registered['baseline_id']
        self.sid=self.s.open(self.token,self.bid,'warehouse-zone-01','fixture-http',True)['stream_id']

    def reading(self,index=0,abnormal=True,**updates):
        self.tick=datetime(2026,10,14,10,0,tzinfo=timezone.utc).timestamp()+index
        return {'message_id':'reading-'+str(index),'timestamp':datetime.fromtimestamp(self.tick,timezone.utc).isoformat(),
                'asset_id':'warehouse-zone-01','synthetic':True,'vibration_rms_mm_s':4.5 if abnormal else 2.,'temperature_c':63. if abnormal else 55.,**updates}

    def accept(self,index=0,abnormal=True,**updates):return self.s.accept(self.token,self.sid,'sensor',self.reading(index,abnormal,**updates))

    def log(self,index=3,code='MOTOR_OVERLOAD',level='WARN',identifier=None):
        body=self.reading(index)
        return {k:v for k,v in body.items() if k not in ('vibration_rms_mm_s','temperature_c')} | {'message_id':identifier or 'log-'+str(index),'code':code,'level':level,'message':'Fixture controller report'}

    def test_causal_three_readings_and_one_event_during_continuous_fault(self):
        first=self.accept(0);second=self.accept(1);third=self.accept(2);fourth=self.accept(3)
        self.assertEqual([x['score']['alert'] for x in [first,second,third,fourth]],[False,False,True,True])
        self.assertIsNotNone(third['event_id']);self.assertIsNone(fourth['event_id'])
        self.assertEqual(len(self.s.history.notices()),1);self.assertEqual(self.s.get(self.token,self.sid)['event_count'],1)
        self.assertEqual(third['confirmation_input_ids'],['reading-0','reading-1','reading-2'])

    def test_batch_detector_candidates_match_stream_scores(self):
        bundle=self.s._bundle(self.bid);rows=[];stream=[]
        for index in range(8):
            body=self.reading(index,index in (1,2,3,6,7));stream.append(self.s.accept(self.token,self.sid,'sensor',body))
            rows.append({'timestamp':body['timestamp'],'asset_id':body['asset_id'],'synthetic':True,'csv_line':None,'values':[body['vibration_rms_mm_s'],body['temperature_c']]})
        batch,_=score_rows(bundle,rows)
        self.assertEqual([r['candidate'] for r in batch],[r['score']['candidate'] for r in stream])
        self.assertEqual([r['alert'] for r in batch],[r['score']['alert'] for r in stream])

    def test_restart_preserves_streak_then_emits_confirmation(self):
        self.accept(0);self.accept(1)
        again=StreamStore(self.root,clock=lambda:self.tick)
        body=self.reading(2);result=again.accept(self.token,self.sid,'sensor',body)
        self.assertTrue(result['score']['alert']);self.assertIsNotNone(result['event_id'])

    def test_idempotent_retry_does_not_advance_streak_or_count(self):
        body=self.reading();first=self.s.accept(self.token,self.sid,'sensor',body)
        self.tick+=100
        retry=self.s.accept(self.token,self.sid,'sensor',body)
        self.assertTrue(retry['replayed']);self.assertEqual(retry['received_at'],first['received_at'])
        self.assertEqual(self.s.get(self.token,self.sid)['input_count'],1)

    def test_conflicting_reused_id_rejected_and_atomic(self):
        self.accept(0);body=self.reading(1,message_id='reading-0')
        with self.assertRaises(StreamConflict):self.s.accept(self.token,self.sid,'sensor',body)
        self.assertEqual(self.s.get(self.token,self.sid)['input_count'],1)

    def test_concurrent_repeated_request_is_one_acceptance(self):
        body=self.reading()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.s.accept(self.token,self.sid,'sensor',body),range(4)))
        self.assertEqual(sum(not r['replayed'] for r in results),1)
        self.assertEqual(self.s.get(self.token,self.sid)['input_count'],1)

    def test_gap_resets_streak_and_continuity_no_recovery_claim(self):
        self.accept(0);self.accept(1);result=self.accept(6)
        self.assertTrue(result['gap_reset']);self.assertFalse(result['score']['alert'])
        self.accept(7);self.assertIsNotNone(self.accept(8)['event_id'])
        self.assertEqual(self.s.get(self.token,self.sid)['gap_count'],1)

    def test_normal_reading_resets_persistence_and_reentry(self):
        for i in range(3):self.accept(i)
        self.accept(3,False)
        for i in (4,5):self.assertFalse(self.accept(i)['score']['alert'])
        self.assertIsNotNone(self.accept(6)['event_id'])
        self.assertEqual(self.s.get(self.token,self.sid)['event_count'],2)

    def test_single_spike_suppressed(self):
        self.accept(0,False);self.accept(1,True);self.accept(2,False)
        self.assertEqual(self.s.history.incidents(),[])

    def test_wrong_asset_or_synthetic_flag_rejected(self):
        for update in [{'asset_id':'machine-b'},{'synthetic':False}]:
            with self.assertRaises(ValueError):self.accept(0,**update)
        self.assertEqual(self.s.get(self.token,self.sid)['input_count'],0)

    def test_invalid_units_values_extra_labels_and_missing_timezone(self):
        for update in [{'vibration_rms_mm_s':-1.},{'vibration_rms_mm_s':float('nan')},{'temperature_c':float('inf')},
                       {'temperature_c':True},{'timestamp':'2026-10-14T10:00:00'},{'anomaly':True}]:
            with self.assertRaises(ValueError):self.accept(0,**update)
        self.assertEqual(self.s.get(self.token,self.sid)['input_count'],0)

    def test_out_of_order_and_repeated_time_rejected(self):
        self.accept(1)
        with self.assertRaises(StreamConflict):self.accept(0)
        with self.assertRaises(StreamConflict):self.accept(1,message_id='another')

    def test_stale_or_future_input_rejected(self):
        for delta in (-31,6):
            body=self.reading();body['timestamp']=datetime.fromtimestamp(self.tick+delta,timezone.utc).isoformat()
            with self.assertRaises(ValueError):self.s.accept(self.token,self.sid,'sensor',body)

    def test_same_timestamp_between_sensor_and_controller_is_allowed(self):
        for index in range(3):self.accept(index)
        result=self.s.accept(self.token,self.sid,'log',self.log(2))
        self.assertIsNotNone(result['event_id'])
        self.assertIn('machine_degradation',{r['kind'] for r in self.s.history.incidents()})

    def test_controller_does_not_confirm_sensor_streak(self):
        self.accept(0);self.s.accept(self.token,self.sid,'log',self.log(1));result=self.accept(2)
        self.assertFalse(result['score']['alert'])

    def test_unmapped_logs_saved_without_invented_event(self):
        result=self.s.accept(self.token,self.sid,'log',self.log(0,'STATUS_OK','INFO'))
        self.assertIsNone(result['event_id']);self.assertEqual(self.s.get(self.token,self.sid)['log_count'],1)
        self.assertEqual(self.s.history.incidents(),[])

    def test_warning_level_mapping_and_shutdown_corroboration(self):
        for index in range(3):self.accept(index)
        self.s.accept(self.token,self.sid,'log',self.log(3,'MOTOR_OVERLOAD','INFO'))
        self.assertEqual(self.s.get(self.token,self.sid)['event_count'],1)
        self.s.accept(self.token,self.sid,'log',self.log(4,'MOTOR_OVERLOAD','WARN'))
        self.s.accept(self.token,self.sid,'log',self.log(5,'PROTECTIVE_SHUTDOWN','ERROR'))
        self.assertEqual({r['kind'] for r in self.s.history.incidents()},{'sensor_anomaly','operational_warning','machine_degradation','abnormal_shutdown'})
        self.assertTrue(all(r['current_report']['hypothesis']['status']=='unconfirmed' for r in self.s.history.incidents()))

    def test_first_snapshot_never_contains_future_controller_facts(self):
        for index in range(3):self.accept(index)
        self.s.accept(self.token,self.sid,'log',self.log(3))
        report=next(r for r in self.s.history.incidents() if r['kind']=='sensor_anomaly')
        self.assertEqual(len(report['first_report']['observed_facts']),1)
        self.assertEqual(len(report['current_report']['observed_facts']),2)

    def test_model_trained_synthetic_propagates_when_input_declares_real(self):
        self.s.close(self.token,self.sid)
        sid=self.s.open(self.token,self.bid,'warehouse-zone-01','declared-real',False)['stream_id']
        for index in range(3):result=self.s.accept(self.token,sid,'sensor',self.reading(index,synthetic=False))
        self.assertTrue(result['synthetic']);self.assertTrue(self.s.get(self.token,sid)['effective_synthetic'])
        self.assertTrue(self.s.history.incidents()[0]['first_report']['synthetic'])

    def test_one_open_stream_per_asset_and_unknown_baseline_asset(self):
        with self.assertRaises(StreamConflict):self.s.open(self.token,self.bid,'warehouse-zone-01','other',True)
        with self.assertRaises(ValueError):self.s.open(self.token,self.bid,'unknown','other',True)
        with self.assertRaises(ValueError):self.s.open(self.token,'../../private','machine-b','other',True)

    def test_close_durable_and_previously_accepted_retry_readable(self):
        body=self.reading();self.s.accept(self.token,self.sid,'sensor',body)
        self.s.close(self.token,self.sid);self.s.close(self.token,self.sid)
        self.assertTrue(self.s.accept(self.token,self.sid,'sensor',body)['replayed'])
        with self.assertRaises(StreamConflict):self.accept(1)
        self.assertEqual(self.s.get(self.token,self.sid)['input_status'],'closed')

    def test_idle_status_is_not_automatic_recovery_or_clear(self):
        for i in range(3):self.accept(i)
        self.tick+=20
        self.assertEqual(self.s.get(self.token,self.sid)['input_status'],'stale')
        self.assertEqual(self.s.history.incidents()[0]['review_state'],'new')

    def test_capacity_rejection_is_atomic(self):
        with self.s.history.connect() as db:db.execute('UPDATE live_streams SET input_count=? WHERE stream_id=?',(MAX_INPUTS,self.sid))
        with self.assertRaises(StreamConflict):self.accept()
        self.assertEqual(self.s.records(self.token,self.sid),[])

    def test_model_hash_change_after_restart_fails_before_acceptance(self):
        path=self.root/'runs/stream-baselines'/self.bid/'sensor.joblib'
        path.write_bytes(b'tampered fixture')
        again=StreamStore(self.root,clock=lambda:self.tick)
        with self.assertRaises(ValueError):again.accept(self.token,self.sid,'sensor',self.reading())
        self.assertEqual(again.get(self.token,self.sid)['input_count'],0)

    def test_tokens_passwords_absent_from_export(self):
        self.accept();snapshot=self.s.export(self.token,self.sid);export=json.dumps(snapshot)
        self.assertEqual(snapshot['baseline']['sha256'],self.registered['sha256'])
        self.assertNotIn(PW,export);self.assertNotIn(self.token,export)
        self.assertIn('received_at',export);self.assertIn('vibration_rms_mm_s',export)

    def test_registered_normal_model_is_not_mutated_by_inference(self):
        before=(self.root/'runs/stream-baselines'/self.bid/'sensor.joblib').read_bytes()
        for i in range(3):self.accept(i)
        self.assertEqual((self.root/'runs/stream-baselines'/self.bid/'sensor.joblib').read_bytes(),before)

    def test_asset_streams_keep_persistence_and_incidents_isolated(self):
        other=self.s.open(self.token,self.bid,'machine-b','other-http',True)['stream_id']
        for index in range(3):
            self.accept(index)
            body=self.reading(index,asset_id='machine-b',vibration_rms_mm_s=8.,temperature_c=72.)
            self.s.accept(self.token,other,'sensor',body)
        self.assertEqual({r['asset_id'] for r in self.s.history.incidents()},{'warehouse-zone-01','machine-b'})
        self.assertEqual(self.s.get(self.token,other)['event_count'],1)

    def test_event_capacity_rolls_back_confirmation_and_input(self):
        self.accept(0);self.accept(1)
        with self.s.history.connect() as db:db.execute('UPDATE live_streams SET event_count=? WHERE stream_id=?',(MAX_EVENTS,self.sid))
        with self.assertRaises(StreamConflict):self.accept(2)
        self.assertEqual(self.s.get(self.token,self.sid)['input_count'],2)
        self.assertEqual(self.s.history.incidents(),[])

    def test_incident_write_failure_rolls_back_entire_input(self):
        self.accept(0);self.accept(1)
        with patch.object(self.s.history,'_save_incident',side_effect=RuntimeError('fixture disk/write failure')):
            with self.assertRaises(RuntimeError):self.accept(2)
        self.assertEqual(self.s.get(self.token,self.sid)['input_count'],2)
        self.assertIsNotNone(self.accept(2)['event_id'])

    def test_operator_can_use_registered_baseline_without_registering_models(self):
        self.a.add_user(self.token,'fixture-operator',PW,'operator');operator=self.a.login('fixture-operator',PW)
        self.s.accept(operator,self.sid,'sensor',self.reading())
        with self.assertRaises(PermissionError):self.s.register(operator,ROOT/'data/sensor-demo/train.csv',ROOT/'data/sensor-demo/validation.csv')

    def test_acquisition_and_receipt_are_separate_and_export_counts_match(self):
        body=self.reading();body['timestamp']=datetime.fromtimestamp(self.tick-1,timezone.utc).isoformat()
        result=self.s.accept(self.token,self.sid,'sensor',body)
        self.assertNotEqual(result['received_at'],body['timestamp'])
        exported=self.s.export(self.token,self.sid)
        self.assertEqual(exported['stream']['input_count'],len(exported['inputs']))

    def test_equal_timestamp_duplicate_with_new_id_does_not_fake_persistence(self):
        self.accept(0)
        with self.assertRaises(StreamConflict):self.accept(0,message_id='new-id')
        self.assertEqual(self.s.get(self.token,self.sid)['input_count'],1)

    def test_viewer_denied_ingestion_close_and_register(self):
        self.a.add_user(self.token,'fixture-viewer',PW,'viewer');viewer=self.a.login('fixture-viewer',PW)
        self.assertEqual(self.s.get(viewer,self.sid)['state'],'open')
        for action in [lambda:self.s.accept(viewer,self.sid,'sensor',self.reading()),lambda:self.s.close(viewer,self.sid),lambda:self.s.register(viewer,ROOT/'data/sensor-demo/train.csv',ROOT/'data/sensor-demo/validation.csv')]:
            with self.assertRaises(PermissionError):action()

    def test_role_revocation_blocks_new_inputs(self):
        self.a.logout(self.token)
        with self.assertRaises(PermissionError):self.accept()

    def test_api_bearer_role_idempotency_and_no_path_input(self):
        app=create_app(self.a.path,self.root);app.state.stream_store=self.s
        with TestClient(app) as client:
            route=f'/v1/streams/{self.sid}/readings';headers={'Authorization':'Bearer '+self.token}
            self.assertEqual(client.post(route,json=self.reading()).status_code,401)
            body=self.reading();response=client.post(route,json=body,headers=headers)
            self.assertEqual(response.status_code,200)
            self.assertTrue(client.post(route,json=body,headers=headers).json()['replayed'])
            self.assertEqual(client.post(route,json={**body,'model_path':'private.joblib'},headers=headers).status_code,422)
            self.a.add_user(self.token,'fixture-viewer',PW,'viewer');viewer=self.a.login('fixture-viewer',PW)
            view={'Authorization':'Bearer '+viewer}
            self.assertEqual(client.post(route,json=self.reading(1),headers=view).status_code,403)
            self.assertEqual(client.get('/v1/streams',headers=view).status_code,200)

    def test_upgrade_step11_to_step12_preserves_profiles_and_same_version_is_noop(self):
        old=self.root/'old';new=self.root/'new'
        for root,version in [(old,'0.11.0'),(new,'0.12.0')]:
            (root/'opsfusion').mkdir(parents=True);(root/'opsfusion/__init__.py').write_text("__version__ = '"+version+"'")
            (root/'dashboard.py').write_text(version)
        (old/'runs').mkdir();(old/'runs/accounts.sqlite3').write_bytes(b'preserved-profile')
        result=upgrade(old,new);self.assertEqual(result['to_version'],'0.12.0')
        self.assertEqual((old/'runs/accounts.sqlite3').read_bytes(),b'preserved-profile')
        self.assertTrue(upgrade(old,new)['already_current'])

    def test_sender_rejects_remote_or_credential_urls(self):
        for url in ['https://example.com','http://user:secret@localhost:8000','http://localhost:8000/path','http://localhost:8000?token=secret']:
            with self.assertRaises(ValueError):local_url(url)


class StreamDashboardTests(unittest.TestCase):
    def test_http_evidence_and_role_controls(self):
        from ui_fixture import authenticated_app
        app=authenticated_app(self,ROOT)
        # Fixture creates a disposable copied dashboard; derive its root from the protected accounts path.
        root=Path(app._script_path).parent
        store=StreamStore(root);token=app.session_state.security_token
        registered=store.register(token,ROOT/'data/sensor-demo/train.csv',ROOT/'data/sensor-demo/validation.csv')
        sid=store.open(token,registered['baseline_id'],'warehouse-zone-01','dashboard-test',True)['stream_id']
        now=datetime.now(timezone.utc)
        for index in range(3):
            store.accept(token,sid,'sensor',{'message_id':'r'+str(index),'timestamp':(now+timedelta(seconds=index)).isoformat(),
                         'asset_id':'warehouse-zone-01','synthetic':True,'vibration_rms_mm_s':4.5,'temperature_c':63.})
        app.selectbox(key='source').select('Live monitoring & history').run()
        self.assertFalse(app.exception)
        self.assertTrue(any('Latest accepted measurements' in s.value for s in app.markdown))
        self.assertTrue(any('Local unread alerts: 1' in s.value for s in app.markdown))
        self.assertTrue(any('no future readings' in s.value for s in app.caption))
        self.assertFalse(app.button(key='http_stream_close').disabled)
        app.button(key='http_stream_close').click().run();self.assertFalse(app.exception)
        self.assertEqual(store.get(token,sid)['state'],'closed')
