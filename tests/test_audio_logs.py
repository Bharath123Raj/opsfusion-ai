import csv,json,tempfile,unittest
from pathlib import Path
from copy import deepcopy
from datetime import timedelta
from opsfusion.audio import read_manifest,fit,score,decode,features,SR,sha
from opsfusion.audio_demo import generate
from opsfusion.audio_eval import evaluate
from opsfusion.logs import parse_logs,FIELDS
from opsfusion.core import Event,fuse

class AudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name)/'demo';generate(cls.root)
        cls.t=read_manifest(cls.root/'train.json');cls.v=read_manifest(cls.root/'validation.json');cls.r=read_manifest(cls.root/'test.json')[0]
        cls.bundle,cls.meta=fit(cls.t,cls.v)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def test_feature_dimensions(self):self.assertEqual(self.r['vectors'].shape,(30,64))
    def test_train_test_separate(self):
        with self.assertRaises(ValueError):score(self.bundle,self.t[0])
    def test_duplicate_calibration_content(self):
        with self.assertRaises(ValueError):fit(self.t,self.t)
    def test_future_inference_required(self):
        record={**self.r,'start':self.v[0]['start']-timedelta(seconds=1)}
        with self.assertRaises(ValueError):score(self.bundle,record)
    def test_asset_isolation(self):
        with self.assertRaises(ValueError):score(self.bundle,{**self.r,'asset_id':'other'})
    def test_confirmed_audio_episodes(self):
        rows,episodes=score(self.bundle,self.r)
        self.assertEqual(len(episodes),2)
        self.assertTrue(all(r['synthetic'] for r in rows))
    def test_no_backdated_confirmation(self):
        rows,episodes=score(self.bundle,self.r)
        for e in episodes:
            self.assertEqual(e['confirmation_window'],e['onset_window']+1)
            i=e['confirmation_window']
            self.assertEqual(rows[i]['available_at'],(self.r['start']+timedelta(seconds=i+1)).isoformat())
    def test_prefix_invariance(self):
        allrows,_=score(self.bundle,self.r)
        prefix={**self.r,'vectors':self.r['vectors'][:15]}
        earlier,_=score(self.bundle,prefix)
        self.assertEqual([r['alert'] for r in earlier],[r['alert'] for r in allrows[:15]])
    def test_invalid_persistence(self):
        with self.assertRaises(ValueError):score(self.bundle,self.r,0)
    def test_reproducible_generation(self):
        with tempfile.TemporaryDirectory() as td:
            dest=Path(td)/'same';generate(dest)
            self.assertEqual(sha(dest/'test-0.wav'),sha(self.root/'test-0.wav'))
    def test_wrong_sample_rate_rejected(self):
        import numpy as np
        from scipy.io import wavfile
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'bad.wav';wavfile.write(p,8000,np.zeros(8000,dtype=np.int16))
            with self.assertRaises(ValueError):decode(p)
    def test_stereo_rejected(self):
        import numpy as np
        from scipy.io import wavfile
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'bad.wav';wavfile.write(p,SR,np.zeros((SR,2),dtype=np.int16))
            with self.assertRaises(ValueError):decode(p)
    def test_short_recording_rejected(self):
        import numpy as np
        from scipy.io import wavfile
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'bad.wav';wavfile.write(p,SR,np.zeros(10,dtype=np.int16))
            with self.assertRaises(ValueError):decode(p)
    def test_manifest_labels_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'bad.json';d=json.loads((self.root/'test.json').read_text());d['clips'][0]['label']='fault';p.write_text(json.dumps(d))
            with self.assertRaises(ValueError):read_manifest(p)
    def test_safe_weights_roundtrip(self):
        import torch
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'audio.pt';torch.save(self.bundle,p);b=torch.load(p,weights_only=True,map_location='cpu')
            self.assertEqual(score(b,self.r)[1],score(self.bundle,self.r)[1])
    def test_audio_standalone_and_corroboration(self):
        a=Event('a','2026-01-01T10:00:06Z','m','audio','abnormal_noise','mic','file:///a.json','Acoustic deviation',1,True)
        l=Event('l','2026-01-01T10:00:07Z','m','log','motor_warning','ctl','file:///l.csv','Controller warning',1,True)
        types={i['type'] for i in fuse([a,l],standalone_audio_logs=True)['incidents']}
        self.assertEqual(types,{'acoustic_anomaly','operational_warning','machine_degradation'})
        self.assertTrue(all(i['hypothesis']['status']=='unconfirmed' for i in fuse([a,l])['incidents']))

class LogTests(unittest.TestCase):
    def file(self,root,rows):
        path=Path(root)/'logs.csv'
        with path.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=sorted(FIELDS));w.writeheader();w.writerows(rows)
        return path
    def row(self,**kw):
        return {'timestamp':'2026-01-01T10:00:00Z','asset_id':'m','source_id':'ctl','level':'WARN','code':'TEMP_WARNING','message':'Observed warning','synthetic':'true',**kw}
    def test_warning_mapping(self):
        with tempfile.TemporaryDirectory() as td:
            events,_=parse_logs(self.file(td,[self.row()]));self.assertEqual(events[0].kind,'motor_warning')
    def test_unknown_code_not_guessed(self):
        with tempfile.TemporaryDirectory() as td:
            events,meta= parse_logs(self.file(td,[self.row(code='UNKNOWN',message='motor is overheating shutdown now')]))
            self.assertEqual(events,[]);self.assertEqual(len(meta['unmapped_records']),1)
    def test_info_warning_not_alert(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(parse_logs(self.file(td,[self.row(level='INFO')]))[0],[])
    def test_duplicate_dedup(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(len(parse_logs(self.file(td,[self.row(),self.row()]))[0]),1)
    def test_missing_timezone(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):parse_logs(self.file(td,[self.row(timestamp='2026-01-01T10:00:00')]))
    def test_shutdown_mapping(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(parse_logs(self.file(td,[self.row(code='PROTECTIVE_SHUTDOWN',level='ERROR')]))[0][0].kind,'shutdown')
