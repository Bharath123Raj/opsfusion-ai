import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from opsfusion import raw_ingest as raw
from opsfusion.app_service import run_events

ROOT = Path(__file__).resolve().parents[1]
LOG_HEADER = 'timestamp,asset_id,source_id,level,code,message,synthetic\n'


def controller(code='MOTOR_OVERLOAD', synthetic=False):
    return raw.UploadedInput('controller.csv', (LOG_HEADER + f'2026-01-01T10:00:07Z,machine-01,plc-01,WARN,{code},Controller source report,{str(synthetic).lower()}\n').encode())


def video_fixture():
    import cv2
    import numpy as np
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'fixture.avi'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 5, (100, 100))
        if not writer.isOpened():
            raise RuntimeError('Test video encoder unavailable')
        for _ in range(6):
            writer.write(np.zeros((100, 100, 3), dtype=np.uint8))
        writer.release()
        return raw.UploadedInput('fixture.avi', path.read_bytes())


class ScriptedDetector:
    metadata = {'name': 'raw adapter fixture', 'test_only': True}
    def __init__(self):
        self.index = 0
    def __call__(self, frame):
        x = .3 if self.index < 2 else .4
        self.index += 1
        return [{'box': [x, .2, x + .3, .8], 'confidence': .9}]


class RawValidationTests(unittest.TestCase):
    def test_no_inputs_does_not_create_run(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'run'
            with self.assertRaises(ValueError):
                raw.run_inputs(raw.RawInputs(), path)
            self.assertFalse(path.exists())

    def test_logs_to_fusion_preserve_source_flags(self):
        with tempfile.TemporaryDirectory() as td:
            record = raw.run_inputs(raw.RawInputs(logs=controller()), Path(td) / 'run')
            self.assertEqual(len(record['events']), 1)
            self.assertFalse(record['events'][0]['synthetic'])
            self.assertEqual(run_events(record['events'])['replay']['first_alerts'][0]['type'], 'operational_warning')
            self.assertEqual(record['adapters']['logs']['input_rows'], 1)

    def test_unknown_log_code_does_not_invent_anomaly(self):
        with tempfile.TemporaryDirectory() as td:
            record = raw.run_inputs(raw.RawInputs(logs=controller('HEARTBEAT')), Path(td) / 'run')
            self.assertEqual(record['events'], [])
            self.assertEqual(len(record['adapters']['logs']['unmapped_records']), 1)
            self.assertEqual(run_events(record['events'])['explanations'], [])

    def test_missing_baseline_has_no_demo_fallback(self):
        r = raw.RawInputs(sensor=raw.UploadedInput('capture.csv', b'a,b\n1,2\n'))
        with self.assertRaisesRegex(ValueError, 'normal training'):
            raw.validate_inputs(r)

    def test_untrusted_local_model_never_loads(self):
        with tempfile.TemporaryDirectory() as td:
            model = Path(td) / 'untrusted.joblib'
            model.write_bytes(b'not a model')
            r = raw.RawInputs(sensor=raw.UploadedInput('capture.csv', b'a,b\n1,2\n'), sensor_model=model)
            with patch('joblib.load', side_effect=AssertionError('Must not deserialize')):
                with self.assertRaisesRegex(ValueError, 'Confirm'):
                    raw.run_inputs(r, Path(td) / 'run')

    def test_training_requires_normal_period_review(self):
        r = raw.RawInputs(sensor=raw.UploadedInput('capture.csv', b'csv'), sensor_train=raw.UploadedInput('train.csv', b'csv'),
                          sensor_validation=raw.UploadedInput('validation.csv', b'csv'))
        with self.assertRaisesRegex(ValueError, 'known normal'):
            raw.validate_inputs(r)

    def test_upload_name_cannot_choose_output_path(self):
        with tempfile.TemporaryDirectory() as td:
            upload = raw.UploadedInput('../../escape.csv', controller().data)
            record = raw.run_inputs(raw.RawInputs(logs=upload), Path(td) / 'run')
            self.assertTrue((Path(td) / 'run/inputs/controller_logs.csv').is_file())
            self.assertFalse((Path(td) / 'escape.csv').exists())
            self.assertEqual(record['sources']['controller_logs']['original_name'], '../../escape.csv')

    def test_artifact_export_and_path_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            record = raw.run_inputs(raw.RawInputs(logs=controller()), Path(td) / 'run')
            with self.assertRaises(ValueError):
                raw.artifact_path(record, '../outside.csv')
            with zipfile.ZipFile(io.BytesIO(raw.export_artifacts(record))) as z:
                self.assertIn('combined_events.jsonl', z.namelist())
                self.assertIn('inputs/controller_logs.csv', z.namelist())
                self.assertEqual(json.loads(z.read('raw_run_manifest.json'))['status'], 'completed')

    def test_failed_adapter_does_not_claim_completed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'run'
            with self.assertRaises(ValueError):
                raw.run_inputs(raw.RawInputs(logs=raw.UploadedInput('bad.csv', b'wrong,columns\n1,2\n')), path)
            manifest = json.loads((path / 'raw_run_manifest.json').read_text())
            self.assertEqual(manifest['status'], 'failed')
            self.assertTrue(manifest['outputs_are_incomplete'])

    def test_limits_and_extensions(self):
        for upload in [raw.UploadedInput('capture.exe', b'bad'), raw.UploadedInput('capture.csv', b'x' * (5 * raw.MIB + 1))]:
            with self.assertRaises(ValueError):
                raw.validate_inputs(raw.RawInputs(logs=upload))
        with self.assertRaisesRegex(ValueError, 'data rows'):
            raw.check_file(raw.UploadedInput('capture.csv', b'a\n' + b'1\n' * 20001), ('.csv',), 5 * raw.MIB, 20000)


@unittest.skipUnless(importlib.util.find_spec('cv2') and importlib.util.find_spec('numpy'), 'Install vision dependencies')
class RawVideoTests(unittest.TestCase):
    def setUp(self):
        self.request = raw.RawInputs(video=video_fixture(), video_start='2026-01-01T15:30:00+05:30',
                                     polygon=[[.5, .1], [.9, .1], [.9, .95], [.5, .95]])

    def test_decode_detect_evidence_and_review(self):
        with tempfile.TemporaryDirectory() as td:
            record = raw.run_inputs(self.request, Path(td) / 'run', detector=ScriptedDetector())
            self.assertEqual(record['adapters']['video']['sampled_frames'], 6)
            self.assertEqual(len(record['events']), 1)
            self.assertTrue(record['events'][0]['synthetic'])  # Scripted detections never pass as real inference.
            self.assertTrue(record['events'][0]['timestamp'].startswith('2026-01-01T10:00:'))
            event = record['events'][0]
            self.assertTrue(raw.artifact_path(record, record['evidence'][event['event_id']]['media']).is_file())
            self.assertEqual(run_events(record['events'])['replay']['first_alerts'][0]['type'], 'restricted_zone')
            with zipfile.ZipFile(io.BytesIO(raw.export_artifacts(record))) as z:
                self.assertIn('video/annotated.mp4', z.namelist())
                self.assertNotIn('inputs/camera_capture.avi', z.namelist())
            with zipfile.ZipFile(io.BytesIO(raw.export_artifacts(record, True))) as z:
                self.assertIn('inputs/camera_capture.avi', z.namelist())

    def test_zone_preview_decodes_first_frame(self):
        picture, metadata = raw.preview_video(self.request.video, self.request.polygon)
        self.assertTrue(picture.startswith(b'\xff\xd8'))
        self.assertEqual(metadata['frames'], 6)

    def test_recording_time_and_zone_are_required(self):
        for changes in [{'video_start': ''}, {'video_start': '2026-01-01T10:00:00'}, {'polygon': [[0, 0], [1, 1]]}, {'max_video_seconds': 61}, {'sample_fps': 0}]:
            r = copy.copy(self.request)
            for key, value in changes.items():
                setattr(r, key, value)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                raw.validate_inputs(r)


@unittest.skipUnless(all(importlib.util.find_spec(m) for m in ('cv2', 'torch', 'scipy', 'sklearn')), 'Install perception dependencies')
class RawMultimodalTests(unittest.TestCase):
    def test_four_adapters_train_infer_and_fuse_uploaded_files(self):
        from scipy.io import wavfile
        import numpy as np
        a = ROOT / 'data/audio-demo'
        s = ROOT / 'data/sensor-demo'
        _, first = wavfile.read(a / 'train-0.wav')
        _, second = wavfile.read(a / 'train-1.wav')
        memory = io.BytesIO()
        wavfile.write(memory, 16000, np.concatenate([first, second]))
        r = raw.RawInputs(video=video_fixture(), video_start='2026-01-01T10:00:00Z',
            polygon=[[.5, .1], [.9, .1], [.9, .95], [.5, .95]], asset_id='warehouse-zone-01',
            sensor=raw.UploadedInput('test.csv', (s / 'test.csv').read_bytes()),
            sensor_train=raw.UploadedInput('train.csv', (s / 'train.csv').read_bytes()),
            sensor_validation=raw.UploadedInput('validation.csv', (s / 'validation.csv').read_bytes()),
            audio=raw.UploadedInput('test.wav', (a / 'test-0.wav').read_bytes()), audio_start='2026-01-01T10:00:00Z', audio_synthetic=False,
            audio_train=raw.UploadedInput('train.wav', memory.getvalue()), audio_train_start='2026-01-01T09:56:40Z',
            audio_validation=raw.UploadedInput('validation.wav', (a / 'validation-0.wav').read_bytes()), audio_validation_start='2026-01-01T09:58:20Z',
            audio_baseline_synthetic=True, normal_periods_reviewed=True,
            logs=raw.UploadedInput('logs.csv', (a / 'logs.csv').read_bytes()))
        with tempfile.TemporaryDirectory() as td:
            record = raw.run_inputs(r, Path(td) / 'run', detector=ScriptedDetector())
            self.assertEqual(set(record['adapters']), {'sensor', 'audio', 'video', 'logs'})
            self.assertEqual({e['modality'] for e in record['events']}, {'sensor', 'audio', 'video', 'log'})
            self.assertTrue(all(e['synthetic'] for e in record['events']))
            bundle = run_events(record['events'])
            self.assertTrue(any(i['type'] == 'machine_degradation' for i in bundle['replay']['final_report']['incidents']))
            self.assertTrue(all(x['cause_status'] == 'unconfirmed' for x in bundle['explanations']))
            # Reuse only the locally trained baseline; generated baseline provenance persists.
            other = raw.RawInputs(sensor=r.sensor, sensor_model=Path(record['models']['sensor']), trust_local_models=True)
            scored = raw.run_inputs(other, Path(td) / 'rerun')
            self.assertTrue(all(e['synthetic'] for e in scored['events']))


try:
    from streamlit.testing.v1 import AppTest
    UI_AVAILABLE = True
except ImportError:
    UI_AVAILABLE = False


@unittest.skipUnless(UI_AVAILABLE, 'Install requirements-app.txt')
class RawDashboardTests(unittest.TestCase):
    def app(self):
        from ui_fixture import authenticated_app
        return authenticated_app(self,ROOT)
    def test_defaults_to_real_uploads_with_no_fabricated_results(self):
        at = self.app()
        self.assertFalse(at.exception)
        self.assertEqual(at.selectbox(key='source').value, 'Real recordings')
        self.assertEqual(len(at.metric), 0)
        self.assertTrue(at.button(key='run').disabled)

    def test_missing_capture_is_visible_error(self):
        at = self.app()
        at.button(key='raw_analyse').click().run()
        self.assertFalse(at.exception)
        self.assertTrue(any('Upload the selected video' in e.value for e in at.error))

    def test_other_sources_expose_baseline_and_log_controls(self):
        at = self.app()
        at.checkbox(key='raw_video_on').uncheck()
        at.checkbox(key='raw_sensor_on').check().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.radio(key='raw_sensor_method').value, 'Train from normal files')
        at.checkbox(key='raw_audio_on').check().run()
        self.assertEqual(at.radio(key='raw_audio_method').value, 'Train from normal files')
        at.checkbox(key='raw_logs_on').check().run()
        self.assertFalse(at.exception)


if __name__ == '__main__':
    unittest.main()
