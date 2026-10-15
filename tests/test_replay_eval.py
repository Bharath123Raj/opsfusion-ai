import json,tempfile,unittest
from pathlib import Path
from copy import deepcopy
from dataclasses import replace
from opsfusion.core import Event
from opsfusion.replay import replay
from opsfusion.incident_eval import evaluate,validate_labels
from opsfusion.incident_demo import generate
from opsfusion.__main__ import load_events


def event(eid,seconds,modality,kind,asset='m'):
    return Event(eid,f'2026-01-01T10:00:{seconds:02d}Z',asset,modality,kind,modality,f'synthetic://{eid}','Synthetic observation',1.,True)

class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.sensor=event('s',0,'sensor','sensor_anomaly');self.audio=event('a',4,'audio','abnormal_noise');self.log=event('l',7,'log','motor_warning')
    def test_first_alert_has_no_future_evidence(self):
        r=replay([self.sensor,self.audio,self.log]);a=next(a for a in r['first_alerts'] if a['type']=='machine_degradation')
        self.assertTrue(a['first_emitted_at'].endswith('00:04+00:00'))
        self.assertNotIn('l',a['available_event_ids']);self.assertNotIn('l',a['snapshot']['trigger_event_ids'])
        self.assertIn('l',next(i for i in r['final_report']['incidents'] if i['type']=='machine_degradation')['trigger_event_ids'])
    def test_prefix_snapshot_unchanged(self):
        short=replay([self.sensor,self.audio]);full=replay([self.sensor,self.audio,self.log])
        byid={a['alert_id']:a for a in full['first_alerts']}
        for a in short['first_alerts']:self.assertEqual(a,byid[a['alert_id']])
    def test_equal_time_order_invariant(self):
        audio=replace(self.audio,timestamp=self.sensor.timestamp)
        self.assertEqual(replay([self.sensor,audio]),replay([audio,self.sensor]))
    def test_unordered_files_deterministic(self):
        self.assertEqual(replay([self.sensor,self.audio,self.log]),replay([self.log,self.sensor,self.audio]))
    def test_duplicate_event_deduplicated(self):self.assertEqual(replay([self.sensor]),replay([self.sensor,self.sensor]))
    def test_conflicting_duplicate_rejected(self):
        with self.assertRaises(ValueError):replay([self.sensor,replace(self.sensor,summary='conflict')])
    def test_stable_run_identifier(self):self.assertEqual(replay([self.sensor])['run_id'],replay([self.sensor])['run_id'])
    def test_changed_input_changes_run(self):self.assertNotEqual(replay([self.sensor])['run_id'],replay([replace(self.sensor,summary='another')])['run_id'])
    def test_empty_replay(self):self.assertEqual(replay([])['first_alerts'],[])
    def test_updates_do_not_duplicate_alerts(self):
        r=replay([self.sensor,self.audio,self.log]);self.assertEqual(sum(a['type']=='machine_degradation' for a in r['first_alerts']),1)
        self.assertEqual(sum('machine_degradation' in u['incident_id'] for u in r['updates']),2)

class EvaluationTests(unittest.TestCase):
    def labels(self):return {'schema':'opsfusion_incident_labels_v1','scope':'test','synthetic':True,'evaluation_types':['restricted_zone'],
        'incidents':[{'label_id':'t','asset_id':'m','type':'restricted_zone','start':'2026-01-01T10:00:00Z','end':'2026-01-01T10:00:10Z'}],
        'coverage':[{'asset_id':'m','start':'2026-01-01T10:00:00Z','end':'2026-01-01T10:01:00Z','normal':False}]}
    def pred(self,eid,seconds,asset='m'):return {'alert_id':eid,'asset_id':asset,'type':'restricted_zone','first_emitted_at':f'2026-01-01T10:00:{seconds:02d}Z'}
    def test_one_to_one_duplicate_predictions(self):
        m=evaluate({'first_alerts':[self.pred('a',1),self.pred('b',2)]},self.labels())
        self.assertEqual((m['tp'],m['fp'],m['fn']),(1,1,0))
    def test_missing_label_is_fn(self):self.assertEqual(evaluate({'first_alerts':[]},self.labels())['fn'],1)
    def test_wrong_type_not_matched(self):
        p={**self.pred('a',1),'type':'abnormal_shutdown'};m=evaluate({'first_alerts':[p]},self.labels())
        self.assertEqual(m['tp'],0);self.assertIn('abnormal_shutdown',m['ignored_report_categories'])
    def test_outside_coverage_refused(self):
        with self.assertRaises(ValueError):evaluate({'first_alerts':[self.pred('a',1,'other')]},self.labels())
    def test_late_alert_requires_explicit_tolerance(self):
        r={'first_alerts':[self.pred('a',12)]};self.assertEqual(evaluate(r,self.labels())['tp'],0)
        self.assertEqual(evaluate(r,self.labels(),2)['tp'],1)
    def test_early_alert_not_matched(self):
        labels=self.labels();labels['incidents'][0]['start']='2026-01-01T10:00:05Z'
        self.assertEqual(evaluate({'first_alerts':[self.pred('a',1)]},labels,10)['tp'],0)
    def test_maximum_matching(self):
        labels=self.labels();labels['incidents'][0]['end']='2026-01-01T10:00:02Z'
        labels['incidents'].append({'label_id':'u','asset_id':'m','type':'restricted_zone','start':'2026-01-01T10:00:01Z','end':'2026-01-01T10:00:04Z'})
        r={'first_alerts':[self.pred('a',1),self.pred('b',2)]}
        self.assertEqual(evaluate(r,labels)['tp'],2)
    def test_duplicate_label_rejected(self):
        l=self.labels();l['incidents'].append(dict(l['incidents'][0]))
        with self.assertRaises(ValueError):validate_labels(l)
    def test_normal_exposure_false_alert_rate(self):
        l=self.labels();l['incidents']=[];l['coverage'][0]['normal']=True
        m=evaluate({'first_alerts':[self.pred('a',1)]},l)
        self.assertEqual(m['false_alerts_per_normal_asset_hour'],60.)
    def test_zero_exposure_not_zero_rate(self):
        m=evaluate({'first_alerts':[]},self.labels());self.assertIsNone(m['false_alerts_per_normal_asset_hour'])
    def test_overlap_coverage_rejected(self):
        l=self.labels();l['coverage'].append(dict(l['coverage'][0]))
        with self.assertRaises(ValueError):validate_labels(l)
    def test_authored_benchmark_known_limit(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'demo';generate(root)
            events=load_events(root/'events.jsonl');labels=json.loads((root/'labels.json').read_text())
            m=evaluate(replay(events),labels)
            self.assertEqual((m['tp'],m['fp'],m['fn']),(7,0,1))
            self.assertEqual(m['missed_labels'],['label-09-1'])
            self.assertNotIn('label_id',events[0].to_dict())

class ReplayReportTests(unittest.TestCase):
    def test_html_escapes_source_text(self):
        from opsfusion.replay import render_report
        e=replace(event('v',0,'video','zone_entry'),summary='<script>bad()</script>',evidence_ref='javascript:bad()')
        html=render_report(replay([e]))
        self.assertNotIn('<script>',html);self.assertIn('&lt;script&gt;',html)
        self.assertNotIn('href="javascript:',html)
    def test_report_saved(self):
        from opsfusion.replay import save_replay
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'run';r=replay([event('v',0,'video','zone_entry')]);save_replay(r,p)
            self.assertTrue((p/'report.html').is_file());self.assertEqual(json.loads((p/'run_manifest.json').read_text())['run_id'],r['run_id'])

class LabelCoverageConsistencyTests(unittest.TestCase):
    def test_fault_label_cannot_be_declared_normal(self):
        labels=EvaluationTests().labels();labels['coverage'][0]['normal']=True
        with self.assertRaises(ValueError):validate_labels(labels)
