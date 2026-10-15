import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile
import tempfile

from opsfusion import app_service as service
from opsfusion.core import parse_time

ROOT = Path(__file__).resolve().parents[1]


def corpus(name='incident-demo'):
    folder = ROOT / 'data' / name
    rows = [e.to_dict() for e in service.load_event_upload((folder / 'events.jsonl').read_bytes())]
    return rows, json.loads((folder / 'labels.json').read_text())


class AppServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.labels = corpus()
        cls.bundle = service.run_events(cls.rows)

    def test_first_scope_freezes_citations(self):
        self.assertEqual(len(self.bundle['explanations']), 26)
        first = {a['alert_id']: a for a in self.bundle['replay']['first_alerts']}
        for r in self.bundle['explanations']:
            a = first[r['incident_id']]
            self.assertEqual(r['cause_status'], 'unconfirmed')
            self.assertFalse(r['automatic_action_executed'])
            self.assertTrue(all(parse_time(e['timestamp']) <= parse_time(a['first_emitted_at']) for e in r['observed_source_reports']))
            ids = {e['event_id'] for e in r['observed_source_reports']}
            self.assertTrue(all(set(x['evidence_ids']) <= ids for x in r['possible_explanations'] + r['recommended_checks']))

    def test_final_scope_is_explicit(self):
        reports = service.explanations(self.bundle['replay'], 'final')
        self.assertTrue(all(r['scope'] == 'final' for r in reports))
        first_sizes = {r['incident_id']: len(r['observed_source_reports']) for r in self.bundle['explanations']}
        self.assertTrue(any(len(r['observed_source_reports']) > first_sizes[r['incident_id']] for r in reports))

    def test_metrics_match_prior_steps(self):
        b = service.benchmark_events(self.rows, self.labels)
        self.assertEqual([(r['tp'], r['fp'], r['fn']) for r in b['comparison']], [(7, 0, 1), (8, 0, 0)])
        self.assertEqual(len(b['ablations']), 9)
        self.assertTrue(b['metrics']['episode']['ignored_report_categories'])

    def test_regression_keeps_known_failures(self):
        rows, labels = corpus('segmentation-regression')
        b = service.benchmark_events(rows, labels)
        self.assertEqual([(r['tp'], r['fp'], r['fn']) for r in b['comparison']], [(6, 4, 1), (6, 1, 1)])

    def test_export_can_reproduce_run(self):
        b = service.benchmark_events(self.rows, self.labels)
        with zipfile.ZipFile(io.BytesIO(service.export_run(self.bundle, b))) as z:
            exported = json.loads(z.read('replay.json'))
            rows = [e.to_dict() for e in service.load_event_upload(z.read('events.jsonl'))]
            self.assertEqual(service.run_events(rows)['replay'], exported)
            self.assertEqual(json.loads(z.read('run_manifest.json'))['live_model_calls'], 0)
            self.assertIn('comparison.csv', z.namelist())
            self.assertIn('explanations_report.html', z.namelist())
            self.assertTrue(all(not name.startswith('/') and '..' not in name for name in z.namelist()))

    def test_html_escapes_uploaded_source(self):
        rows = copy.deepcopy(self.rows)
        rows[0]['summary'] = '<script>alert(1)</script>'
        bundle = service.run_events(rows)
        with zipfile.ZipFile(io.BytesIO(service.export_run(bundle))) as z:
            for name in ['replay_report.html', 'explanations_report.html']:
                self.assertNotIn(b'<script>alert(1)</script>', z.read(name))
                self.assertIn(b'&lt;script&gt;', z.read(name))

    def test_conflicting_duplicates_fail(self):
        duplicate = dict(self.rows[0], summary='Changed source')
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            service.run_events([self.rows[0], duplicate])

    def test_identical_duplicates_deduplicate(self):
        self.assertEqual(len(service.run_events([self.rows[0], self.rows[0]])['events']), 1)

    def test_upload_bom_and_line_error(self):
        raw = b'\xef\xbb\xbf' + json.dumps(self.rows[0]).encode() + b'\n\n'
        self.assertEqual(len(service.load_event_upload(raw)), 1)
        with self.assertRaisesRegex(ValueError, 'Line 3'):
            service.load_event_upload(raw + b'bad json')

    def test_upload_size_and_event_cap(self):
        with self.assertRaises(ValueError):
            service.load_event_upload(b'x' * (service.MAX_BYTES + 1))
        with self.assertRaises(ValueError):
            service.validate_events([self.rows[0]] * (service.MAX_EVENTS + 1))

    def test_event_field_types_are_strict(self):
        for field, value in [('timestamp', True), ('asset_id', 3), ('confidence', True), ('synthetic', 'true')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                service.run_events([dict(self.rows[0], **{field: value})])

    def test_nonfinite_json_and_settings_rejected(self):
        with self.assertRaises(ValueError):
            service.decode_json(b'{"value":NaN}')
        for value in [True, float('nan'), 0, -1, 3601]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                service.run_events([], quiet_gap_seconds=value)

    def test_empty_and_low_confidence_no_alert(self):
        self.assertEqual(service.run_events([])['explanations'], [])
        self.assertEqual(service.run_events([dict(self.rows[0], confidence=0.1)])['explanations'], [])

    def test_invalid_labels_fail_before_matching(self):
        for labels in [[], {}, dict(self.labels, synthetic='true'), dict(self.labels, incidents=[{}]), dict(self.labels, coverage=None)]:
            with self.subTest(labels=labels), self.assertRaises(ValueError):
                service.benchmark_events(self.rows, labels)


try:
    from fastapi.testclient import TestClient
    from opsfusion.api import create_app
    API_AVAILABLE = True
except ImportError:
    API_AVAILABLE = False


@unittest.skipUnless(API_AVAILABLE, 'Install requirements-app.txt for API tests')
class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from opsfusion.accounts import Accounts
        cls.temp=tempfile.TemporaryDirectory();cls.accounts=Accounts(Path(cls.temp.name)/'accounts.sqlite3')
        cls.accounts.bootstrap('fixture-admin','Fixture password 123!')
        token=cls.accounts.login('fixture-admin','Fixture password 123!')
        cls.client = TestClient(create_app(cls.accounts.path),headers={'Authorization':'Bearer '+token})
        cls.rows, cls.labels = corpus()

    @classmethod
    def tearDownClass(cls):
        cls.client.close();cls.temp.cleanup()

    def test_health_and_openapi(self):
        self.assertEqual(self.client.get('/health').json()['live_model_calls'], 0)
        self.assertIn('/v1/replay', self.client.get('/openapi.json').json()['paths'])
        self.assertEqual(self.client.get('/v1/contract').json()['max_events'], 200)

    def test_api_replay_matches_shared_service(self):
        for engine in ['fixed', 'episode']:
            for settings in [{}, {'window_seconds': 90.0, 'quiet_gap_seconds': 20.0}]:
                with self.subTest(engine=engine, settings=settings):
                    response = self.client.post('/v1/replay', json={'events': self.rows, 'engine': engine, **settings})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), service.run_events(self.rows, engine=engine))

    def test_unknown_and_invalid_fields(self):
        cases = [{'events': self.rows, 'file_path': 'C:/secret'}, {'events': [dict(self.rows[0], synthetic='true')]},
                 {'events': [dict(self.rows[0], confidence=True)]}, {'events': [dict(self.rows[0], timestamp='2026-01-01')]},
                 {'events': [dict(self.rows[0], modality='unknown')]}, {'events': [dict(self.rows[0], asset_id=' ')]},
                 {'events': [], 'quiet_gap_seconds': True}, {'events': [], 'engine': 'other'}]
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual(self.client.post('/v1/replay', json=body).status_code, 422)

    def test_conflicting_id_returns_422(self):
        rows = [self.rows[0], dict(self.rows[0], summary='conflict')]
        self.assertEqual(self.client.post('/v1/replay', json={'events': rows}).status_code, 422)

    def test_request_size_limit(self):
        response = self.client.post('/v1/replay', content=b'x' * (service.MAX_BYTES + 1))
        self.assertEqual(response.status_code, 413)

    def test_chunked_request_size_limit(self):
        def chunks():
            for _ in range(3):
                yield b'x' * (1024 * 1024)
        response = self.client.post('/v1/replay', content=chunks())
        self.assertEqual(response.status_code, 413)

    def test_event_and_benchmark_caps(self):
        self.assertEqual(self.client.post('/v1/replay', json={'events': [self.rows[0]] * 201}).status_code, 422)
        self.assertEqual(self.client.post('/v1/benchmark', json={'events': [self.rows[0]] * 101, 'labels': self.labels}).status_code, 422)

    def test_explain_first_final_and_unknown(self):
        bundle = service.run_events(self.rows)
        ident = bundle['explanations'][0]['incident_id']
        for scope in ['first', 'final']:
            response = self.client.post('/v1/explain', json={'events': self.rows, 'incident_id': ident, 'scope': scope})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['scope'], scope)
            self.assertEqual(response.json()['cause_status'], 'unconfirmed')
        self.assertEqual(self.client.post('/v1/explain', json={'events': [], 'incident_id': 'unknown'}).status_code, 404)

    def test_benchmark_and_bad_labels(self):
        response = self.client.post('/v1/benchmark', json={'events': self.rows, 'labels': self.labels})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['comparison'][1]['tp'], 8)
        for labels in [{}, dict(self.labels, coverage=None), dict(self.labels, evaluation_types=[{}])]:
            self.assertEqual(self.client.post('/v1/benchmark', json={'events': self.rows, 'labels': labels}).status_code, 422)

    def test_evidence_paths_are_not_opened(self):
        row = dict(self.rows[0], evidence_ref='file:///nonexistent/private/evidence.wav')
        with patch('pathlib.Path.read_bytes', side_effect=AssertionError('Unexpected file read')):
            response = self.client.post('/v1/replay', json={'events': [row]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['events'][0]['evidence_ref'], row['evidence_ref'])

    def test_export_zip_and_empty_run(self):
        response = self.client.post('/v1/export', json={'events': []})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], 'application/zip')
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            self.assertEqual(json.loads(z.read('explanations.json')), [])


try:
    from streamlit.testing.v1 import AppTest
    STREAMLIT_AVAILABLE = True
except ImportError:
    STREAMLIT_AVAILABLE = False


@unittest.skipUnless(STREAMLIT_AVAILABLE, 'Install requirements-app.txt for dashboard tests')
class DashboardTests(unittest.TestCase):
    def setUp(self):
        from ui_fixture import authenticated_app
        self.at = authenticated_app(self,ROOT)
        self.at.selectbox(key='source').select('Synthetic incident demo').run()
        self.assertFalse(self.at.exception)

    def test_default_demo_and_exports(self):
        self.assertEqual(self.at.metric[0].value, '24')
        self.assertEqual(self.at.metric[2].value, '26')
        self.assertEqual(len(self.at.tabs), 5)
        self.assertEqual(self.at.session_state['bundle']['live_model_calls'], 0)
        self.assertTrue(self.at.warning)

    def test_engine_change_requires_run(self):
        self.at.selectbox(key='engine').select('fixed').run()
        self.assertTrue(any('Inputs changed' in w.value for w in self.at.warning))
        self.assertEqual(self.at.session_state['bundle']['replay']['configuration']['engine'], 'episode')
        self.at.button(key='run').click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.session_state['bundle']['replay']['configuration']['engine'], 'fixed')

    def test_benchmark_and_invalidation_on_new_run(self):
        self.at.button(key='benchmark_run').click().run()
        self.assertEqual(self.at.session_state['benchmark']['comparison'][1]['tp'], 8)
        self.at.selectbox(key='source').select('Synthetic segmentation regression').run()
        self.at.button(key='run').click().run()
        self.assertFalse(self.at.exception)
        self.assertIsNone(self.at.session_state['benchmark'])
        self.assertEqual(self.at.metric[0].value, '49')

    def test_fixed_run_can_compare_both_engines(self):
        self.at.selectbox(key='engine').select('fixed').run()
        self.at.button(key='run').click().run()
        self.at.button(key='benchmark_run').click().run()
        self.assertFalse(self.at.exception)
        self.assertFalse(self.at.error)
        self.assertEqual(len(self.at.session_state['benchmark']['comparison']), 2)

    def test_final_scope_has_explicit_label(self):
        self.at.radio(key='scope').set_value('final').run()
        self.assertFalse(self.at.exception)
        self.assertTrue(any('final scope' in x.value for x in self.at.markdown))

    def test_replay_cursor_removes_future_alerts(self):
        times = self.at.select_slider(key='cursor').options
        self.at.select_slider(key='cursor').set_value(times[0]).run()
        self.assertFalse(self.at.exception)
        expected = sum(parse_time(a['first_emitted_at']) <= parse_time(times[0]) for a in self.at.session_state['bundle']['replay']['first_alerts'])
        self.assertTrue(any(f'{expected} first alerts available' in x.value for x in self.at.markdown))

    def test_missing_upload_does_not_replace_successful_run(self):
        old = self.at.session_state['bundle']['replay']['run_id']
        self.at.selectbox(key='source').select('Upload adapter events').run()
        self.at.button(key='run').click().run()
        self.assertFalse(self.at.exception)
        self.assertTrue(self.at.error)
        self.assertEqual(self.at.session_state['bundle']['replay']['run_id'], old)

    def test_mixed_precision_timestamps_render_together(self):
        rows, _ = corpus()
        rows = [dict(rows[0], timestamp='2026-01-01T10:00:00.125Z'),
                dict(rows[1], timestamp='2026-01-01T10:00:01Z')]
        self.at.session_state['bundle'] = service.run_events(rows)
        self.at.session_state['labels'] = None
        self.at.run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.metric[0].value, '2')


if __name__ == '__main__':
    unittest.main()
