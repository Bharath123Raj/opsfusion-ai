from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from opsfusion.accounts import Accounts
from opsfusion.api import create_app
from opsfusion.core import Event
from opsfusion.correlation import WatchStore, MAX_WATCH_EVENTS
from opsfusion.history import History
from opsfusion.live import settings
from opsfusion.stream_store import StreamStore
from opsfusion.stream import send_demo, StreamClientError
from opsfusion.upgrade import upgrade

ROOT=Path(__file__).resolve().parents[1]
PW='Correlation fixture password 123!'
ZONE={'asset_id':'warehouse-zone-01','camera_id':'fixture-camera','polygon':[[.5,.1],[.9,.1],[.9,.95],[.5,.95]],'confirm_samples':2,'max_age_seconds':1,'min_iou':.15}


class CorrelationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base=tempfile.TemporaryDirectory();cls.seed=Path(cls.base.name)
        a=Accounts(cls.seed/'runs/accounts.sqlite3');a.bootstrap('fixture-admin',PW);t=a.login('fixture-admin',PW)
        s=StreamStore(cls.seed);cls.bid=s.register(t,ROOT/'data/sensor-demo/train.csv',ROOT/'data/sensor-demo/validation.csv')['baseline_id']

    @classmethod
    def tearDownClass(cls):cls.base.cleanup()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        shutil.copytree(self.seed/'runs',self.root/'runs')
        self.tick=datetime(2026,10,15,10,tzinfo=timezone.utc)
        self.clock=patch('opsfusion.history.utc_now',lambda:self.tick.isoformat());self.clock.start();self.addCleanup(self.clock.stop)
        self.s=StreamStore(self.root,clock=lambda:self.tick.timestamp());self.h=self.s.history;self.a=self.s.accounts
        self.token=self.a.login('fixture-admin',PW);self.w=WatchStore(self.root)
        self.sid=self.s.open(self.token,self.bid,ZONE['asset_id'],'fixture-http',True)['stream_id']
        self.camera=self.h.start(ZONE['camera_id'],ZONE['asset_id'],'webcam',settings(ZONE))
        self.wid=self.w.open(self.token,self.camera,self.sid)['watch_id']

    def advance(self,seconds):self.tick+=timedelta(seconds=seconds)

    def video(self,identifier='camera-1',offset=0,kind='zone_entry',synthetic=False):
        e=Event(identifier,(self.tick+timedelta(seconds=offset)).isoformat(),ZONE['asset_id'],'video',kind,ZONE['camera_id'],'file:///not-present.jpg','Fixture zone observation',.9,synthetic)
        self.h.ingest(self.camera,[e]);return e

    def log(self,identifier='log-1',offset=0,code='MOTOR_OVERLOAD'):
        return self.s.accept(self.token,self.sid,'log',{'message_id':identifier,'timestamp':(self.tick+timedelta(seconds=offset)).isoformat(),
                    'asset_id':ZONE['asset_id'],'synthetic':True,'level':'WARN','code':code,'message':'Fixture controller report'})

    def reading(self,index,abnormal=True):
        return self.s.accept(self.token,self.sid,'sensor',{'message_id':'reading-'+str(index),'timestamp':self.tick.isoformat(),
                    'asset_id':ZONE['asset_id'],'synthetic':True,'vibration_rms_mm_s':4.5 if abnormal else 2.,'temperature_c':63. if abnormal else 55.})

    def reports(self):return [r for r in self.h.incidents() if r['session_id']==self.wid]

    def test_joint_report_needs_both_selected_sources(self):
        self.video();self.assertEqual(self.reports(),[])
        self.advance(2);self.log();r=self.reports()[0]['current_report']
        self.assertEqual(r['type'],'zone_machine_cooccurrence');self.assertEqual({e['modality'] for e in r['timeline']},{'video','log'})
        self.assertTrue(r['synthetic']);self.assertEqual(r['hypothesis']['status'],'unconfirmed');self.assertFalse(r['automatic_action_executed'])
        self.assertNotIn('machine_degradation',{r['kind'] for r in self.h.incidents()})

    def test_no_link_means_existing_sources_stay_independent(self):
        self.w.close(self.token,self.wid);self.video();self.advance(1);self.log()
        self.assertEqual(self.reports(),[])

    def test_reverse_arrival_and_late_cross_source_time(self):
        self.log();self.advance(8);self.video(offset=-7)
        self.assertEqual(len(self.reports()),1)
        self.assertEqual(len(self.reports()[0]['first_report']['timeline']),2)

    def test_fixed_anchor_window_does_not_chain(self):
        self.video();self.advance(9);self.log();first=self.reports()[0]
        self.advance(9);self.log('log-2');current=self.reports()[0]
        self.assertEqual(first['current_report'],current['current_report'])
        self.assertEqual(len(current['current_report']['timeline']),2)

    def test_window_boundary_inclusive(self):
        self.video();self.advance(10);self.log();self.assertEqual(len(self.reports()),1)

    def test_beyond_window_does_not_pair(self):
        self.video();self.advance(10.01);self.log();self.assertEqual(self.reports(),[])

    def test_first_snapshot_frozen_on_new_evidence(self):
        self.video();self.advance(1);self.log();before=self.reports()[0]
        self.advance(1);self.log('log-2');after=self.reports()[0]
        self.assertEqual(before['first_report'],after['first_report'])
        self.assertEqual(len(after['current_report']['timeline']),3)
        self.assertEqual(after['current_report']['detected_at'],before['first_report']['detected_at'])

    def test_resolved_joint_report_reopens_with_new_evidence(self):
        self.video();self.advance(1);self.log();r=self.reports()[0]
        self.h.review(r['incident_id'],'resolved','fixture-admin','Reviewed',r['revision'],authenticated=True)
        self.advance(1);self.log('log-2');after=self.reports()[0]
        self.assertEqual(after['review_state'],'new')
        self.assertEqual(len([n for n in self.h.notices() if n['incident_id']==r['incident_id']]),2)

    def test_retries_do_not_duplicate_pair_or_notice(self):
        e=self.video();self.advance(1);self.log();before=self.reports()[0]
        self.h.ingest(self.camera,[e]);self.log()
        self.assertEqual(self.reports()[0],before)
        self.assertEqual(self.w.get(self.token,self.wid)['event_count'],2)

    def test_two_camera_anchors_create_two_joint_reports(self):
        self.video();self.advance(1);self.log();self.advance(1);self.video('camera-2')
        self.assertEqual(len(self.reports()),2)

    def test_initial_presence_preserved_without_invented_entry(self):
        self.video(kind='zone_presence');self.advance(1);self.log()
        r=self.reports()[0]['current_report']
        self.assertEqual(r['correlation']['camera_observation_kind'],'zone_presence')
        self.assertEqual(r['timeline'][0]['kind'],'zone_presence')

    def test_camera_gap_prevents_old_signal_pairing(self):
        self.log();self.advance(1);self.h.boundary(self.camera);self.advance(1);self.video()
        self.assertEqual(self.reports(),[]);self.assertEqual(self.w.get(self.token,self.wid)['reset_count'],1)
        self.advance(1);self.log('log-2');self.assertEqual(len(self.reports()),1)

    def test_sensor_gap_prevents_old_camera_pairing(self):
        self.video();self.reading(0,False);self.advance(6);self.reading(1)
        self.advance(1);self.reading(2);self.advance(1);self.reading(3)
        self.assertEqual(self.reports(),[]);self.assertEqual(self.w.get(self.token,self.wid)['reset_count'],1)

    def test_pre_link_timestamps_are_not_backfilled(self):
        self.video(offset=-1);self.advance(1);self.log()
        self.assertEqual(self.reports(),[]);self.assertEqual(self.w.get(self.token,self.wid)['excluded_count'],1)

    def test_pre_link_stored_events_never_backfilled(self):
        self.w.close(self.token,self.wid);self.video();self.advance(1)
        self.wid=self.w.open(self.token,self.camera,self.sid)['watch_id'];self.advance(1);self.log()
        self.assertEqual(self.reports(),[])

    def test_stale_camera_is_saved_but_excluded_from_live_pairing(self):
        self.advance(40);self.video(offset=-31);self.log()
        self.assertEqual(self.reports(),[]);self.assertEqual(self.w.get(self.token,self.wid)['excluded_count'],1)
        self.assertEqual(self.h.session(self.camera)['event_count'],1)

    def test_future_camera_excluded(self):
        self.video(offset=6);self.log();self.assertEqual(self.reports(),[])

    def test_wrong_asset_link_rejected(self):
        other=self.h.start('other-camera','machine-b','webcam',settings(ZONE))
        with self.assertRaises(ValueError):self.w.open(self.token,other,self.sid)

    def test_unselected_camera_cannot_contribute(self):
        other=self.h.start('other-camera',ZONE['asset_id'],'webcam',settings(ZONE))
        e=Event('other-event',self.tick.isoformat(),ZONE['asset_id'],'video','zone_entry','other-camera','file:///not-present.jpg','Other camera',1.,True)
        self.h.ingest(other,[e]);self.log();self.assertEqual(self.reports(),[])

    def test_duplicate_links_rejected(self):
        with self.assertRaises(ValueError):self.w.open(self.token,self.camera,self.sid)

    def test_invalid_horizon_and_inactive_sources_rejected(self):
        self.w.close(self.token,self.wid)
        for horizon in [0,31,True,float('nan')]:
            with self.assertRaises(ValueError):self.w.open(self.token,self.camera,self.sid,horizon)
        self.h.finish(self.camera)
        with self.assertRaises(ValueError):self.w.open(self.token,self.camera,self.sid)

    def test_close_source_closes_link_without_recovery_claim(self):
        self.s.close(self.token,self.sid)
        self.assertEqual(self.w.get(self.token,self.wid)['close_reason'],'linked_source_closed')
        self.assertEqual(self.w.get(self.token,self.wid)['state'],'closed')

    def test_stop_camera_closes_link(self):
        self.h.request_stop(self.camera);self.assertEqual(self.w.get(self.token,self.wid)['state'],'closed')

    def test_camera_finish_closes_link(self):
        self.h.finish(self.camera);self.assertEqual(self.w.get(self.token,self.wid)['state'],'closed')

    def test_watch_capacity_closes_only_watch_and_source_continues(self):
        with self.h.connect() as db:db.execute('UPDATE asset_watches SET event_count=? WHERE watch_id=?',(MAX_WATCH_EVENTS,self.wid))
        self.video();self.assertEqual(self.w.get(self.token,self.wid)['close_reason'],'watch_capacity_reached')
        self.assertEqual(self.h.session(self.camera)['event_count'],1)

    def test_joint_transaction_failure_rolls_back_camera_and_notices(self):
        self.log();self.advance(1)
        with patch('opsfusion.correlation._report',side_effect=OSError('Fixture disk error')):
            with self.assertRaises(OSError):self.video()
        self.assertEqual(self.h.session(self.camera)['event_count'],0)
        self.assertEqual(self.w.get(self.token,self.wid)['event_count'],1)
        self.assertEqual(self.reports(),[])

    def test_joint_transaction_failure_rolls_back_http_input(self):
        self.video();self.advance(1)
        with patch('opsfusion.correlation._report',side_effect=OSError('Fixture disk error')):
            with self.assertRaises(OSError):self.log()
        self.assertEqual(self.s.get(self.token,self.sid)['input_count'],0)
        self.assertEqual(self.w.get(self.token,self.wid)['event_count'],1)

    def test_restart_retains_link_and_first_report(self):
        self.video();self.advance(1);self.log();before=self.reports()[0]
        again=WatchStore(self.root);self.assertEqual(again.export(self.token,self.wid)['incidents'][0]['first_report'],before['first_report'])
        self.advance(1);self.log('log-2');self.assertEqual(self.reports()[0]['first_report'],before['first_report'])

    def test_export_contains_supporting_three_readings_and_baseline(self):
        self.video()
        for i in range(3):self.advance(1);self.reading(i)
        data=self.w.export(self.token,self.wid)
        self.assertEqual(len(data['supporting_inputs']),3);self.assertEqual(data['baseline']['baseline_id'],self.bid)
        serialized=json.dumps(data);self.assertNotIn(PW,serialized);self.assertNotIn(self.token,serialized)
        self.assertTrue(data['incidents'][0]['current_report']['synthetic'])

    def test_viewer_read_only_and_revoked_token(self):
        self.a.add_user(self.token,'fixture-viewer',PW,'viewer');v=self.a.login('fixture-viewer',PW)
        self.assertEqual(self.w.get(v,self.wid)['watch_id'],self.wid)
        self.assertTrue(self.w.export(v,self.wid))
        with self.assertRaises(PermissionError):self.w.close(v,self.wid)
        self.a.logout(v)
        with self.assertRaises(PermissionError):self.w.watches(v)

    def test_operator_can_link_and_close(self):
        self.w.close(self.token,self.wid);self.a.add_user(self.token,'fixture-operator',PW,'operator');t=self.a.login('fixture-operator',PW)
        w=self.w.open(t,self.camera,self.sid);self.w.close(t,w['watch_id'])

    def test_concurrent_identical_video_retry_cannot_duplicate_report(self):
        self.log();e=Event('concurrent',self.tick.isoformat(),ZONE['asset_id'],'video','zone_entry',ZONE['camera_id'],'file:///missing.jpg','Fixture',1.,True)
        with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(lambda _:self.h.ingest(self.camera,[e]),range(3)))
        self.assertEqual(len(self.reports()),1);self.assertEqual(self.w.get(self.token,self.wid)['event_count'],2)

    def test_authenticated_api_contract_and_role(self):
        self.a.add_user(self.token,'fixture-viewer',PW,'viewer');v=self.a.login('fixture-viewer',PW)
        with TestClient(create_app(project_root=self.root)) as client:
            self.assertEqual(client.get('/v1/watches').status_code,401)
            head={'Authorization':'Bearer '+v}
            self.assertEqual(client.get('/v1/watches',headers=head).status_code,200)
            self.assertEqual(client.post('/v1/watches/'+self.wid+'/close',headers=head).status_code,403)
            self.assertEqual(client.get('/v1/watches/'+self.wid+'/export',headers=head).status_code,200)
            self.assertEqual(client.post('/v1/watches',headers={'Authorization':'Bearer '+self.token},json={'camera_session':self.camera,'http_stream':self.sid,'path':'secret'}).status_code,422)

    def test_installation_identity_stable_and_different_for_other_root(self):
        self.assertEqual(self.w.installation_id(self.token),WatchStore(self.root).installation_id(self.token))
        with tempfile.TemporaryDirectory() as td:
            other=WatchStore(td);other.accounts.bootstrap('fixture-admin',PW);t=other.accounts.login('fixture-admin',PW)
            self.assertNotEqual(other.installation_id(t),self.w.installation_id(self.token))

    def test_sanitized_login_error_exposes_no_password(self):
        import httpx
        def reject(request):return httpx.Response(401,json={'detail':'Sign-in failed'})
        with httpx.Client(transport=httpx.MockTransport(reject),base_url='http://127.0.0.1:8000') as client:
            with self.assertRaises(StreamClientError) as caught:send_demo('http://127.0.0.1:8000','fixture-admin',PW,client=client)
        self.assertIn('Sign-in failed',str(caught.exception));self.assertNotIn(PW,str(caught.exception))

    def test_demo_detects_wrong_installation_before_opening_any_sources(self):
        from opsfusion.watch import run_demo
        client=TestClient(create_app(project_root=self.root))
        with tempfile.TemporaryDirectory() as td,patch('httpx.Client',return_value=client):
            with self.assertRaises(StreamClientError) as caught:
                run_demo(td,'http://127.0.0.1:8000','fixture-admin',PW,paced=False)
        self.assertIn('different installation folders',str(caught.exception))
        self.assertNotIn(PW,str(caught.exception))
        self.assertEqual(len(self.s.streams(self.token)),1)

    def test_upgrade_step12_to_step13_preserves_runs_models_and_same_version_noop(self):
        old=self.root/'old';new=self.root/'new'
        for root,version in [(old,'0.12.0'),(new,'0.13.0')]:
            (root/'opsfusion').mkdir(parents=True);(root/'opsfusion/__init__.py').write_text("__version__ = '"+version+"'")
            (root/'dashboard.py').write_text(version)
        (old/'runs').mkdir();(old/'runs/accounts.sqlite3').write_bytes(b'profile')
        (old/'models').mkdir();(old/'models/retained').write_text('model')
        self.assertEqual(upgrade(old,new)['to_version'],'0.13.0')
        self.assertEqual((old/'runs/accounts.sqlite3').read_bytes(),b'profile')
        self.assertEqual((old/'models/retained').read_text(),'model');self.assertTrue(upgrade(old,new)['already_current'])


if __name__=='__main__':unittest.main()
