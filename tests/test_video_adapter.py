import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from opsfusion.video import run

@unittest.skipUnless(importlib.util.find_spec('cv2') and importlib.util.find_spec('numpy'),'Optional OpenCV dependencies not installed')
class AdapterTests(unittest.TestCase):
    def test_video_decode_evidence_and_fusion(self):
        import cv2
        import numpy as np
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);video=root/'fixture.avi'
            w=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'MJPG'),5,(100,100))
            self.assertTrue(w.isOpened())
            for _ in range(6):w.write(np.zeros((100,100,3),dtype=np.uint8))
            w.release()
            zone=root/'zone.json';zone.write_text(json.dumps({'asset_id':'z','camera_id':'c','polygon':[[.5,.1],[.9,.1],[.9,.95],[.5,.95]]}))
            class FakeDetector:
                metadata={'name':'scripted fixture detections','test_only':True}
                def __init__(self):self.n=0
                def __call__(self,frame):
                    x=.3 if self.n<2 else .4;self.n+=1
                    return [{'box':[x,.2,x+.3,.8],'confidence':.9}]
            args=SimpleNamespace(video=video,zone=zone,start_time='2026-01-01T10:00:00Z',output=root/'run',sample_fps=5,conf=.6,max_seconds=None,merge_events=None,model='unused',device='cpu',synthetic_video=True)
            report=run(args,FakeDetector())
            self.assertEqual(report['incidents'][0]['type'],'restricted_zone')
            manifest=json.loads((args.output/'run_manifest.json').read_text())
            self.assertEqual(manifest['sampled_frames'],6);self.assertEqual(manifest['entry_observations'],1)
            self.assertTrue(list((args.output/'evidence').glob('*.jpg')))
            self.assertTrue((args.output/'annotated.mp4').is_file())
            self.assertTrue(report['incidents'][0]['synthetic'])
            with self.assertRaises(ValueError):run(args,FakeDetector())
