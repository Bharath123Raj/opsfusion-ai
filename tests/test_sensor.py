import csv
import json
from pathlib import Path
import tempfile
import unittest
from copy import deepcopy
from opsfusion.sensor import read_csv,FIELDS,confirm_flags,fit_baselines,score_rows,evaluate
from opsfusion.sensor_demo import generate
from opsfusion.core import Event,fuse

class SensorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name)/'demo';generate(cls.root)
        cls.train=read_csv(cls.root/'train.csv');cls.val=read_csv(cls.root/'validation.csv');cls.test=read_csv(cls.root/'test.csv')
        cls.bundle,cls.meta=fit_baselines(cls.train,cls.val)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def test_demo_counts(self):self.assertEqual((len(self.train),len(self.val),len(self.test)),(1600,400,800))
    def test_chronological_split(self):
        with self.assertRaises(ValueError):fit_baselines(self.train,self.train)
    def test_asset_mismatch(self):
        with self.assertRaises(ValueError):fit_baselines(self.train,[r for r in self.val if r['asset_id']=='machine-b'])
    def test_insufficient_train(self):
        with self.assertRaises(ValueError):fit_baselines(self.train[:5],self.val[:5])
    def test_future_only_inference(self):
        with self.assertRaises(ValueError):score_rows(self.bundle,self.val)
    def test_unknown_asset(self):
        rows=deepcopy(self.test[:2]);rows[0]['asset_id']='unknown'
        with self.assertRaises(ValueError):score_rows(self.bundle,rows)
    def test_causal_confirmation(self):
        rs=self.test[:4];flags,episodes=confirm_flags(rs,[True]*4,3,2)
        self.assertEqual(flags,[False,False,True,True]);self.assertEqual(episodes[0]['confirmation_index'],2)
    def test_one_sample_suppressed(self):
        flags,episodes=confirm_flags(self.test[:3],[False,True,False],3,2)
        self.assertEqual(flags,[False]*3);self.assertEqual(episodes,[])
    def test_gap_resets(self):
        rows=[self.test[0],self.test[1],self.test[5],self.test[6]]
        flags,episodes=confirm_flags(rows,[True]*4,3,2)
        self.assertFalse(any(flags));self.assertEqual(episodes,[])
    def test_prefix_invariance(self):
        full,_=score_rows(self.bundle,self.test)
        prefix=[r for r in self.test if r['asset_id']=='machine-b'][:150]
        short,_=score_rows(self.bundle,prefix)
        bykey={(r['asset_id'],r['timestamp']):r['alert'] for r in full}
        self.assertEqual([r['alert'] for r in short],[bykey[(r['asset_id'],r['timestamp'])] for r in short])
    def test_scale_floor(self):
        self.assertTrue(all(min(m['scale'])>0 for m in self.bundle['models'].values()))
    def test_model_not_changed_on_score(self):
        original={k:float(v['threshold']) for k,v in self.bundle['models'].items()}
        score_rows(self.bundle,self.test)
        self.assertEqual(original,{k:float(v['threshold']) for k,v in self.bundle['models'].items()})
    def test_synthetic_propagation(self):
        scored,_=score_rows(self.bundle,self.test)
        self.assertTrue(all(r['synthetic'] for r in scored))
    def test_evaluation_coverage(self):
        scored,_=score_rows(self.bundle,self.test)
        m=evaluate(scored,self.root/'labels.json')
        self.assertEqual(sum(m[k] for k in ('tp','fp','tn','fn')),800)
        self.assertTrue(m['synthetic'])
    def test_sensor_only_incident(self):
        e=Event('s1','2026-01-01T10:00:00Z','a','sensor','sensor_anomaly','s','file:///evidence.json','Sensor deviation',1,True)
        self.assertEqual(fuse([e])['incidents'][0]['type'],'sensor_anomaly')
    def test_multimodal_corroboration(self):
        s=Event('s1','2026-01-01T10:00:00Z','a','sensor','sensor_anomaly','s','file:///evidence.json','Sensor deviation',1,True)
        l=Event('l1','2026-01-01T10:00:01Z','a','log','motor_warning','l','file:///log.json','Motor warning',1,True)
        self.assertIn('machine_degradation',{i['type'] for i in fuse([s,l])['incidents']})
    def bad_csv(self,mutate):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'bad.csv'
            r={'timestamp':'2026-01-01T10:00:00Z','asset_id':'a','vibration_rms_mm_s':'2','temperature_c':'55','synthetic':'true'}
            fields=list(FIELDS);rows=[r];mutate(fields,rows)
            with path.open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
            with self.assertRaises(ValueError):read_csv(path)
    def test_missing_value(self):self.bad_csv(lambda f,rs:rs[0].update(temperature_c=''))
    def test_nan(self):self.bad_csv(lambda f,rs:rs[0].update(temperature_c='nan'))
    def test_negative_rms(self):self.bad_csv(lambda f,rs:rs[0].update(vibration_rms_mm_s='-1'))
    def test_duplicate_times(self):self.bad_csv(lambda f,rs:rs.append(dict(rs[0])))
    def test_naive_time(self):self.bad_csv(lambda f,rs:rs[0].update(timestamp='2026-01-01T10:00:00'))
    def test_label_column_rejected(self):
        def mutation(fields,rows):fields.append('anomaly');rows[0]['anomaly']='true'
        self.bad_csv(mutation)
    def test_invalid_synthetic(self):self.bad_csv(lambda f,rs:rs[0].update(synthetic='maybe'))
    def test_constant_training_floor(self):
        train=deepcopy(self.train);val=deepcopy(self.val)
        for r in train+val:r['values']=[2.,55.]
        b,_=fit_baselines(train,val)
        for m in b['models'].values():self.assertEqual(m['scale'].tolist(),[.05,.25])
    def test_interval_matching(self):
        scored,_=score_rows(self.bundle,self.test)
        m=evaluate(scored,self.root/'labels.json')['event_metrics']
        self.assertEqual(m['expected_fault_intervals'],6)
        self.assertEqual(m['matched'],5)
        self.assertEqual(m['missed_faults'][0]['fault_type'],'single_sample_spike')
    def test_protected_existing_output(self):
        from types import SimpleNamespace
        from opsfusion.sensor import infer
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);marker=p/'run_manifest.json';marker.write_text('preserve me')
            with self.assertRaises(ValueError):infer(SimpleNamespace(output=p))
            self.assertEqual(marker.read_text(),'preserve me')
