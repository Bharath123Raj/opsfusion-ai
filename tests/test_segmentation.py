import json,tempfile,unittest
from pathlib import Path
from dataclasses import replace
from datetime import timedelta
from opsfusion.core import Event,parse_time
from opsfusion.segmentation import fuse_episodes
from opsfusion.replay import replay
from opsfusion.incident_eval import evaluate
from opsfusion.segmentation_demo import generate
from opsfusion.__main__ import load_events


def event(eid,seconds,modality,kind,asset='m',confidence=1):
    t=parse_time('2026-01-01T10:00:00Z')+timedelta(seconds=seconds)
    return Event(eid,t.isoformat(),asset,modality,kind,modality,f'synthetic://{eid}','Synthetic observation',confidence,True)

def machine_alerts(events,**kw):return [a for a in replay(events,engine='episode',**kw)['first_alerts'] if a['type']=='machine_degradation']

class EpisodeTests(unittest.TestCase):
    def setUp(self):self.s=event('s',0,'sensor','sensor_anomaly');self.a=event('a',4,'audio','abnormal_noise')
    def test_two_nearby_faults_split(self):
        es=[self.s,self.a,event('s2',40,'sensor','sensor_anomaly'),event('a2',44,'audio','abnormal_noise')]
        self.assertEqual(len(machine_alerts(es)),2)
        self.assertEqual(len([a for a in replay(es)['first_alerts'] if a['type']=='machine_degradation']),1)
    def test_short_gap_not_split(self):
        self.assertEqual(len(machine_alerts([self.s,self.a,event('s2',16,'sensor','sensor_anomaly'),event('a2',19,'audio','abnormal_noise')])),1)
    def test_exact_gap_boundary_continues(self):self.assertEqual(len(machine_alerts([self.s,replace(self.a,timestamp=event('t',20,'audio','abnormal_noise').timestamp)])),1)
    def test_gap_above_threshold_separates(self):self.assertEqual(machine_alerts([self.s,event('a',20.001,'audio','abnormal_noise')]),[])
    def test_context_does_not_bridge(self):
        notes=[event(f'n{i}',i,'note','operator_note') for i in (10,20,30)]
        self.assertEqual(machine_alerts([self.s,*notes,event('a2',40,'audio','abnormal_noise')]),[])
    def test_safety_does_not_bridge(self):
        video=[event(f'v{i}',i,'video','zone_entry') for i in (10,20,30)]
        self.assertEqual(machine_alerts([self.s,*video,event('a2',40,'audio','abnormal_noise')]),[])
    def test_long_continuous_episode_has_one_alert(self):
        es=[self.s,self.a]+[event(f's{i}',i,'sensor','sensor_anomaly') for i in range(10,161,10)]+[event('a2',104,'audio','abnormal_noise')]
        self.assertEqual(len(machine_alerts(es)),1)
    def test_corroboration_horizon_excludes_stale_source(self):
        es=[self.s]+[event(f'stop{i}',i,'log','shutdown') for i in (20,40,60,80)]+[event('a2',100,'audio','abnormal_noise')]
        self.assertEqual(machine_alerts(es),[])
    def test_assets_remain_separate(self):self.assertEqual(machine_alerts([self.s,replace(self.a,asset_id='other')]),[])
    def test_below_threshold_cannot_extend_episode(self):
        es=[self.s,self.a,event('low',20,'sensor','sensor_anomaly',confidence=.2),event('s2',35,'sensor','sensor_anomaly'),event('a2',39,'audio','abnormal_noise')]
        self.assertEqual(len(machine_alerts(es)),2)
    def test_context_does_not_change_anchor(self):
        r=replay([self.s,self.a],engine='episode');extended=replay([self.s,self.a,event('n',8,'note','operator_note')],engine='episode')
        a=next(a for a in r['first_alerts'] if a['type']=='machine_degradation')
        self.assertEqual(a,next(b for b in extended['first_alerts'] if b['type']=='machine_degradation'))
    def test_first_alert_excludes_later_episode(self):
        alerts=machine_alerts([self.s,self.a,event('s2',40,'sensor','sensor_anomaly'),event('a2',44,'audio','abnormal_noise')])
        self.assertNotIn('s2',alerts[0]['available_event_ids'])
    def test_duplicate_events_dedup(self):self.assertEqual(fuse_episodes([self.s,self.a]),fuse_episodes([self.s,self.a,self.s]))
    def test_conflicting_duplicates_rejected(self):
        with self.assertRaises(ValueError):fuse_episodes([self.s,replace(self.s,summary='bad')])
    def test_invalid_gap_rejected(self):
        for gap in (0,-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):fuse_episodes([self.s],quiet_gap_seconds=gap)
    def test_run_id_records_configuration(self):self.assertNotEqual(replay([self.s],engine='episode',quiet_gap_seconds=20)['run_id'],replay([self.s],engine='episode',quiet_gap_seconds=30)['run_id'])
    def test_numeric_equivalent_parameters_same_id(self):self.assertEqual(replay([self.s],engine='episode',quiet_gap_seconds=20)['run_id'],replay([self.s],engine='episode',quiet_gap_seconds=20.)['run_id'])
    def test_reverse_input_deterministic(self):self.assertEqual(fuse_episodes([self.s,self.a]),fuse_episodes([self.a,self.s]))
    def test_empty_input(self):self.assertEqual(fuse_episodes([])['incidents'],[])
    def test_evidence_graph_has_unique_nodes(self):
        es=[self.s,self.a,event('v',5,'video','zone_entry'),event('n',7,'note','operator_note')]
        self.assertEqual(len(fuse_episodes(es)['event_graph']['nodes']),4)
    def test_unconfirmed_no_actions(self):
        for i in fuse_episodes([self.s,self.a])['incidents']:
            self.assertEqual(i['hypothesis']['status'],'unconfirmed');self.assertFalse(i['automatic_action_executed'])
    def test_separate_regression_keeps_known_failures(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'r';generate(root);es=load_events(root/'events.jsonl');labels=json.loads((root/'labels.json').read_text())
            m=evaluate(replay(es,engine='episode'),labels)
            self.assertEqual((m['tp'],m['fp'],m['fn']),(6,1,1))
            self.assertEqual(m['missed_labels'],['regression-label-07-0'])
