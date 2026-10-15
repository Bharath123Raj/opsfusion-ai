import copy
from datetime import timedelta
import importlib.util
import json
import queue
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from opsfusion.core import Event, parse_time
from opsfusion.history import History, utc_now
from opsfusion.live import Processor, launch, settings, validate_source, worker


ZONE={'asset_id':'zone-1','camera_id':'camera-1','polygon':[[.5,.1],[.9,.1],[.9,.95],[.5,.95]],
      'confirm_samples':2,'max_age_seconds':1,'min_iou':.15}


def event(identifier='e1', seconds=0, kind='zone_entry', **extra):
    row=dict(event_id=identifier,timestamp=(parse_time('2026-01-01T00:00:00Z')+timedelta(seconds=seconds)).isoformat(),
             asset_id='zone-1',modality='video',kind=kind,source_id='camera-1',
             evidence_ref='file:///not-present.jpg',summary='Camera candidate for review',confidence=.9,synthetic=True)
    row.update(extra)
    return Event(**row)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.h=History(self.root/'history.sqlite3')
        self.sid=self.h.start('camera-1','zone-1','webcam',settings(ZONE))

    def tearDown(self):
        self.temp.cleanup()

    def test_frozen_first_latest_update_and_dedup_notice(self):
        self.h.ingest(self.sid,[event()]);first=self.h.incidents()[0]
        self.h.ingest(self.sid,[event('e2',2)])
        latest=self.h.incidents()[0]
        self.assertEqual(len(latest['first_report']['timeline']),1)
        self.assertEqual(len(latest['current_report']['timeline']),2)
        self.assertEqual(latest['first_report'],first['first_report'])
        self.assertEqual(len(self.h.notices()),1)
        self.h.ingest(self.sid,[event('e2',2)])
        self.assertEqual(self.h.incidents()[0]['revision'],latest['revision'])

    def test_persists_across_instances(self):
        self.h.ingest(self.sid,[event()])
        again=History(self.h.path)
        self.assertEqual(again.incidents(),self.h.incidents())
        self.assertEqual(again.session(self.sid)['event_count'],1)

    def test_only_one_worker_and_release(self):
        with self.assertRaises(ValueError):
            self.h.start('camera-1','zone-1','webcam',settings(ZONE))
        self.h.finish(self.sid)
        self.assertNotEqual(self.h.start('camera-1','zone-1','webcam',settings(ZONE)),self.sid)

    def test_session_can_be_claimed_by_only_one_process(self):
        self.assertTrue(self.h.claim_worker(self.sid,100))
        self.assertFalse(self.h.claim_worker(self.sid,101))
        self.assertEqual(self.h.session(self.sid)['worker_pid'],100)

    def test_stop_blocks_ingestion(self):
        self.h.request_stop(self.sid)
        self.assertTrue(self.h.should_stop(self.sid))
        with self.assertRaises(ValueError):self.h.ingest(self.sid,[event()])
        self.assertEqual(self.h.incidents(),[])

    def test_operator_decision_separate_and_reopens(self):
        self.h.ingest(self.sid,[event()]);r=self.h.incidents()[0]
        self.h.review(r['incident_id'],'resolved','Bharath','Reviewed snapshot',r['revision'])
        closed=self.h.incidents()[0]
        self.assertEqual(closed['current_report'],r['current_report'])
        self.assertEqual(closed['review_state'],'resolved')
        self.h.ingest(self.sid,[event('e2',2)])
        opened=self.h.incidents()[0]
        self.assertEqual(opened['review_state'],'new')
        self.assertEqual([a['action'] for a in self.h.audit(r['incident_id'])],['resolved','reopened'])
        self.assertEqual(len(self.h.notices()),2)

    def test_review_race_and_validation(self):
        self.h.ingest(self.sid,[event()]);r=self.h.incidents()[0]
        self.h.ingest(self.sid,[event('e2',1)])
        with self.assertRaisesRegex(ValueError,'changed'):
            self.h.review(r['incident_id'],'resolved','B','',r['revision'])
        for state,actor,note in [('invalid','B',''),('new','',''),('new','B','x'*2001)]:
            with self.assertRaises(ValueError):self.h.review(r['incident_id'],state,actor,note,2)

    def test_review_audit_and_notice_dismiss_persist(self):
        self.h.ingest(self.sid,[event()]);r=self.h.incidents()[0]
        self.h.review(r['incident_id'],'false_alarm','Operator','Zone needs calibration',r['revision'])
        self.h.dismiss_notice(self.h.notices()[0]['notice_id'])
        again=History(self.h.path)
        self.assertEqual(again.notices(),[])
        self.assertEqual(again.audit(r['incident_id'])[0]['actor'],'Operator')
        self.assertEqual(len(again.incidents('false_alarm')),1)

    def test_gap_and_disconnect_break_continuity(self):
        self.h.ingest(self.sid,[event()]);self.h.ingest(self.sid,[event('e2',21)])
        self.assertEqual(len(self.h.incidents()),2)
        self.h.boundary(self.sid)
        self.h.ingest(self.sid,[event('e3',22,'zone_presence')])
        self.assertEqual(len(self.h.incidents()),3)
        self.assertEqual(self.h.session(self.sid)['connection_epoch'],1)

    def test_exact_quiet_gap_keeps_episode(self):
        self.h.ingest(self.sid,[event()]);self.h.ingest(self.sid,[event('e2',20)])
        self.assertEqual(len(self.h.incidents()),1)

    def test_equal_time_batch_and_distinct_report_types(self):
        self.h.ingest(self.sid,[event('e1',0,'zone_presence'),event('e2',0)])
        self.assertEqual({r['kind'] for r in self.h.incidents()},{'restricted_zone','restricted_zone_presence'})
        self.assertEqual(self.h.session(self.sid)['event_count'],2)

    def test_conflicts_and_source_mismatch_are_atomic(self):
        self.h.ingest(self.sid,[event()])
        for e in [event(summary='different'),event('wrong',2,asset_id='other'),event('wrong',2,modality='sensor',kind='sensor_anomaly')]:
            with self.assertRaises(ValueError):self.h.ingest(self.sid,[e,event('new',3)])
        self.assertEqual(self.h.session(self.sid)['event_count'],1)

    def test_clock_rollback_rejected(self):
        self.h.ingest(self.sid,[event('e1',2)])
        with self.assertRaisesRegex(ValueError,'clock moved backwards'):
            self.h.ingest(self.sid,[event('e2',1)])

    def test_equal_time_must_arrive_as_one_batch(self):
        self.h.ingest(self.sid,[event('e1',2)])
        with self.assertRaisesRegex(ValueError,'one batch'):
            self.h.ingest(self.sid,[event('e2',2)])
        self.assertEqual(len(self.h.incidents()),1)

    def test_concurrent_camera_claim_is_exclusive(self):
        from concurrent.futures import ThreadPoolExecutor
        def claim(_):
            try:return self.h.start('camera-new','zone-1','webcam',settings(ZONE))
            except ValueError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(claim,range(2)))
        self.assertEqual(sum(x is not None for x in results),1)

    def test_capacity_preserves_existing_reports(self):
        cfg=self.h.session(self.sid)['config'];cfg['max_episode_events']=2
        with self.h.connect() as db:db.execute('UPDATE sessions SET config=? WHERE session_id=?',(json.dumps(cfg),self.sid))
        self.h.ingest(self.sid,[event(),event('e2',1)])
        before=self.h.incidents()
        with self.assertRaisesRegex(ValueError,'capacity reached'):self.h.ingest(self.sid,[event('e3',2)])
        self.assertEqual(self.h.incidents(),before)
        self.assertEqual(self.h.session(self.sid)['event_count'],2)

    def test_stale_recovery_is_explicit_and_guarded(self):
        with self.assertRaises(ValueError):self.h.recover_stale(self.sid)
        old=(parse_time(utc_now())-timedelta(seconds=121)).isoformat()
        with self.h.connect() as db:db.execute('UPDATE sessions SET heartbeat=? WHERE session_id=?',(old,self.sid))
        self.h.recover_stale(self.sid)
        self.assertEqual(self.h.session(self.sid)['state'],'interrupted')
        with self.assertRaises(ValueError):self.h.ingest(self.sid,[event()])
        self.h.start('camera-1','zone-1','webcam',settings(ZONE))

    def test_session_boundaries_do_not_merge(self):
        self.h.ingest(self.sid,[event()]);self.h.finish(self.sid)
        sid2=self.h.start('camera-1','zone-1','webcam',settings(ZONE));self.h.ingest(sid2,[event('other',1)])
        self.assertEqual(len(self.h.incidents()),2)

    def test_recorded_run_import_is_idempotent_and_preserves_first(self):
        from opsfusion.app_service import run_events
        bundle=run_events([event().to_dict(),event('e2',1).to_dict()])
        sid=self.h.import_bundle(bundle)
        self.assertEqual(self.h.import_bundle(copy.deepcopy(bundle)),sid)
        r=self.h.incidents()[0]
        self.assertEqual(len(r['first_report']['timeline']),1)
        self.assertEqual(len(r['current_report']['timeline']),2)
        self.assertEqual(self.h.session(sid)['event_count'],2)

    def test_export_has_audit_and_no_credential_settings(self):
        with self.assertRaises(ValueError):
            self.h.start('other','zone-1','rtsp',dict(settings(ZONE),url='rtsp://secret'))
        self.h.ingest(self.sid,[event()])
        out=self.h.export()
        self.assertEqual(len(out['incidents']),1)
        self.assertFalse(out['incidents'][0]['current_report']['automatic_action_executed'])
        self.assertEqual(out['incidents'][0]['current_report']['hypothesis']['status'],'unconfirmed')


class LaunchTests(unittest.TestCase):
    def test_validation(self):
        for mode,source in [('webcam',True),('webcam',-1),('rtsp','https://host'),('rtsp','rtsp://host\nsecret'),('other',0)]:
            with self.assertRaises(ValueError):validate_source(mode,source)
        validate_source('rtsp','rtsp://user:secret@127.0.0.1:554/camera')
        for fps in [0,11,float('nan')]:
            with self.assertRaises(ValueError):settings(ZONE,sample_fps=fps)

    def test_rtsp_secret_not_in_arguments_or_persistence(self):
        with tempfile.TemporaryDirectory() as td,patch('opsfusion.live.subprocess.Popen') as popen:
            root=Path(td);secret='rtsp://operator:secret-token@127.0.0.1:554/camera'
            sid=launch(root,ZONE,'rtsp',secret)
            args=popen.call_args
            self.assertNotIn('secret-token',str(args.args))
            self.assertEqual(args.kwargs['env']['OPSFUSION_RTSP_URL'],secret)
            self.assertNotIn('secret-token',json.dumps(History(root/'runs/history.sqlite3').export()))
            self.assertNotIn('secret-token',(root/'runs/live'/sid/'zone.json').read_text())

    def test_launch_failure_releases_camera(self):
        with tempfile.TemporaryDirectory() as td,patch('opsfusion.live.subprocess.Popen',side_effect=OSError):
            with self.assertRaises(RuntimeError):launch(Path(td),ZONE,'webcam',0)
            self.assertEqual(History(Path(td)/'runs/history.sqlite3').sessions()[0]['state'],'failed')


@unittest.skipUnless(importlib.util.find_spec('torch'),'Optional vision dependency missing')
class WorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.h=History(self.root/'history.sqlite3');self.sid=self.h.start('camera-1','zone-1','webcam',settings(ZONE))

    def tearDown(self):self.temp.cleanup()

    def context(self, messages):
        class Inbox(queue.Queue):
            def close(self):pass
            def cancel_join_thread(self):pass
        class Capture:
            terminated=False
            def __init__(self, **kwargs):self.outbox=kwargs['args'][2]
            def start(self):
                for m in messages:self.outbox.put_nowait(m)
            def terminate(self):self.terminated=True
            def kill(self):self.terminated=True
            def join(self,timeout):pass
            def is_alive(self):return not self.terminated
        class Context:
            def Queue(self,maxsize):return Inbox()
            Process=Capture
        return Context()

    def test_retry_exhaustion_releases_camera_without_frames(self):
        class Detector:metadata={}
        with patch('opsfusion.live.YoloDetector',return_value=Detector()),patch('opsfusion.live.mp.get_context',return_value=self.context([('error','camera_open_failed')])),patch('opsfusion.live._wait',return_value=True):
            result=worker(self.h,self.sid,self.root/'live',ZONE,'webcam',0)
        self.assertEqual(result['state'],'failed')
        self.assertEqual(result['error_code'],'camera_open_failed')
        self.assertEqual(result['connection_epoch'],5)
        self.h.start('camera-1','zone-1','webcam',settings(ZONE))

    def test_empty_eof_is_failure(self):
        class Detector:metadata={}
        with patch('opsfusion.live.YoloDetector',return_value=Detector()),patch('opsfusion.live.mp.get_context',return_value=self.context([('eof',)])):
            result=worker(self.h,self.sid,self.root/'live',ZONE,'webcam',0)
        self.assertEqual(result['state'],'failed')
        self.assertEqual(result['error_code'],'no_frames_received')

    def test_stop_before_model_load(self):
        self.h.request_stop(self.sid)
        with patch('opsfusion.live.YoloDetector') as detector:
            result=worker(self.h,self.sid,self.root/'live',ZONE,'webcam',0)
        detector.assert_not_called()
        self.assertEqual(result['state'],'stopped')

    def test_model_load_failure_releases_claim(self):
        with patch('opsfusion.live.YoloDetector',side_effect=RuntimeError('fixture failure')):
            result=worker(self.h,self.sid,self.root/'live',ZONE,'webcam',0)
        self.assertEqual(result['state'],'failed')
        self.assertEqual(result['error_code'],'detector_or_processing_failed')


@unittest.skipUnless(importlib.util.find_spec('cv2') and importlib.util.find_spec('numpy'),'Optional vision libraries missing')
class FrameTests(unittest.TestCase):
    def test_entry_snapshot_and_disconnect_presence(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);h=History(root/'history.sqlite3');sid=h.start('camera-1','zone-1','webcam',settings(ZONE))
            class Detector:
                metadata={'test_only':True}
                inside=False
                def __call__(self,frame):
                    x=.4 if self.inside else .3
                    return [{'box':[x,.2,x+.3,.8],'confidence':.9}]
            d=Detector();p=Processor(h,sid,root/'live',ZONE,d);frame=np.zeros((100,100,3),dtype=np.uint8)
            for n in range(2):p.process(frame,event(seconds=n*.2).timestamp,n*.2,n)
            d.inside=True
            for n in range(2,4):p.process(frame,event(seconds=n*.2).timestamp,n*.2,n)
            self.assertEqual(h.incidents()[0]['kind'],'restricted_zone')
            self.assertTrue(h.incidents()[0]['current_report']['synthetic'])
            self.assertEqual(len(list((root/'live'/sid/'evidence').glob('*.jpg'))),1)
            p.reset()
            for n in range(4,6):p.process(frame,event(seconds=n*.2).timestamp,n*.2,n)
            self.assertEqual({r['kind'] for r in h.incidents()},{'restricted_zone','restricted_zone_presence'})
            self.assertEqual(len(h.notices()),2)

    def test_safe_evidence_preview(self):
        from opsfusion.live_dashboard import evidence_path
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);allowed=root/'runs/live/sid/evidence/a.jpg';allowed.parent.mkdir(parents=True);allowed.write_bytes(b'fixture')
            secret=root/'private.jpg';secret.write_bytes(b'private')
            self.assertEqual(evidence_path(root,allowed.as_uri()),allowed)
            self.assertIsNone(evidence_path(root,secret.as_uri()))
            self.assertIsNone(evidence_path(root,'https://host/a.jpg'))


@unittest.skipUnless(importlib.util.find_spec('streamlit'),'Optional dashboard dependency missing')
class HistoryDashboardTests(unittest.TestCase):
    def setUp(self):
        from streamlit.testing.v1 import AppTest
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        source=Path(__file__).resolve().parents[1]/'dashboard.py'
        script=self.root/'dashboard.py';script.write_text(source.read_text())
        self.h=History(self.root/'runs/history.sqlite3')
        self.sid=self.h.start('camera-1','zone-1','webcam',settings(ZONE))
        self.h.ingest(self.sid,[event()]);self.h.finish(self.sid)
        from opsfusion.accounts import Accounts
        accounts=Accounts(self.root/'runs/accounts.sqlite3');accounts.bootstrap('bharath','Fixture password 123!')
        self.at=AppTest.from_file(str(script),default_timeout=15)
        self.at.session_state.security_token=accounts.login('bharath','Fixture password 123!')
        self.at.run()
        self.at.selectbox(key='source').select('Live monitoring & history').run()

    def tearDown(self):self.temp.cleanup()

    def test_history_and_first_scope_without_replay(self):
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.selectbox(key='history_incident').value,self.h.incidents()[0]['incident_id'])
        self.at.radio(key='history_scope').set_value('current').run()
        self.assertFalse(self.at.exception)
        self.assertTrue(any('Local unread alerts: 1' in x.value for x in self.at.markdown))

    def test_operator_review_form(self):
        self.at.selectbox(key='history_review_state').select('acknowledged')
        self.at.text_area(key='history_note').set_value('Reviewed generated evidence')
        next(b for b in self.at.button if b.label=='Save review').click().run()
        self.assertFalse(self.at.exception)
        row=self.h.incidents()[0]
        self.assertEqual(row['review_state'],'acknowledged')
        self.assertEqual(self.h.audit(row['incident_id'])[0]['actor'],'bharath')
        self.assertEqual(self.h.audit(row['incident_id'])[0]['actor_origin'],'authenticated_account')

    def test_revision_conflict_shows_refresh_error(self):
        sid=self.h.start('camera-2','zone-1','webcam',settings(ZONE))
        # Change the selected report through an independent operator connection.
        row=self.h.incidents()[0]
        self.h.review(row['incident_id'],'acknowledged','Other operator','',row['revision'])
        self.at.selectbox(key='history_review_state').select('resolved')
        next(b for b in self.at.button if b.label=='Save review').click().run()
        self.assertTrue(any('Report changed' in x.value for x in self.at.error))
        self.assertEqual(self.h.incidents()[0]['review_state'],'acknowledged')
