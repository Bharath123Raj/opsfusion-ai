from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from opsfusion.cloud_worker import LeaseLost
from opsfusion.on_demand import Runtime, SetupError, authorized, run_next, settings_for
from test_cloud_worker import FakeCloud, job_for
from test_raw_ingest import controller

ROOT = Path(__file__).resolve().parents[1]
CODE = 'fixture_processor_access_' + 'x' * 24
SECRETS = {'OPSFUSION_SUPABASE_URL': 'https://blvorawnwbcnujnapmgg.supabase.co',
           'OPSFUSION_SUPABASE_SECRET_KEY': 'sb_secret_' + 'fixture_' * 4,
           'OPSFUSION_PROCESSOR_ACCESS_CODE': CODE}


class QueueCloud(FakeCloud):
    def __init__(self, job, files):
        super().__init__(job, files)
        self.claims = 0; self.closed = False; self.failures = []

    def rpc(self, name, body):
        if name == 'opsfusion_claim_job':
            self.claims += 1
            return [self.job] if self.job else []
        if name == 'opsfusion_fail_job':
            self.failures.append(body)
            return self.valid
        return super().rpc(name, body)

    def close(self):
        self.closed = True


class OnDemandTests(unittest.TestCase):
    def setUp(self):
        self.settings = settings_for(SECRETS)
        self.runtime = Runtime()
        self.files = {'logs': controller()}
        self.client = QueueCloud(job_for(self.files), self.files)

    def run_job(self, **kwargs):
        return run_next(self.settings, self.settings.session_stamp, self.runtime,
                        client_factory=lambda *_: self.client, **kwargs)

    def test_authorization_precedes_privileged_client_creation(self):
        self.assertTrue(authorized(CODE, self.settings))
        for code in ('wrong', None, 'é' * 40): self.assertFalse(authorized(code, self.settings))
        factory = Mock()
        for stamp in (None, '', 'wrong'):
            with self.assertRaises(PermissionError):
                run_next(self.settings, stamp, self.runtime, client_factory=factory)
        factory.assert_not_called()
        changed = settings_for({**SECRETS, 'OPSFUSION_PROCESSOR_ACCESS_CODE': CODE + 'changed'})
        self.assertNotEqual(changed.session_stamp, self.settings.session_stamp)
        self.assertNotIn(SECRETS['OPSFUSION_SUPABASE_SECRET_KEY'], repr(self.settings))

    def test_configuration_rejects_missing_weak_or_frontend_secrets(self):
        for secrets in ({}, {**SECRETS, 'OPSFUSION_PROCESSOR_ACCESS_CODE': 'short'},
                        {**SECRETS, 'OPSFUSION_SUPABASE_SECRET_KEY': 'sb_publishable_' + 'x'*32},
                        {**SECRETS, 'OPSFUSION_PROCESSOR_ACCESS_CODE': SECRETS['OPSFUSION_SUPABASE_SECRET_KEY']}):
            with self.assertRaises(ValueError): settings_for(secrets)

    def test_one_click_processes_actual_controller_and_saves_frozen_reports(self):
        outcome = self.run_job()
        self.assertEqual(outcome['status'], 'succeeded')
        self.assertEqual((outcome['observations'], outcome['incidents']), (1, 1))
        self.assertEqual(self.client.claims, 1)
        self.assertIsNotNone(self.client.finished)
        self.assertTrue(self.client.closed)
        self.assertFalse(self.runtime.lock.locked())

    def test_setup_diagnostics_identify_fields_without_exposing_values(self):
        cases = [('OPSFUSION_SUPABASE_SECRET_KEY', ''),
                 ('OPSFUSION_SUPABASE_SECRET_KEY', 'sb_publishable_' + 'private_fixture_marker'),
                 ('OPSFUSION_SUPABASE_URL', 'https://example.supabase.co:private_fixture_marker'),
                 ('OPSFUSION_PROCESSOR_ACCESS_CODE', 'private_fixture_marker')]
        for name, value in cases:
            with self.subTest(field=name):
                with self.assertRaises(SetupError) as caught:
                    settings_for({**SECRETS, name: value})
                message = str(caught.exception)
                self.assertIn(name, message)
                self.assertNotIn('private_fixture_marker', message)
                self.assertNotIn(SECRETS['OPSFUSION_SUPABASE_SECRET_KEY'], message)

    def test_idle_or_busy_click_never_starts_a_polling_loop(self):
        self.client.job = None
        self.assertEqual(self.run_job()['status'], 'empty')
        self.assertEqual(self.client.claims, 1)
        factory = Mock()
        self.runtime.lock.acquire()
        try:
            outcome = run_next(self.settings, self.settings.session_stamp, self.runtime, client_factory=factory)
        finally:
            self.runtime.lock.release()
        self.assertEqual(outcome['status'], 'busy')
        factory.assert_not_called()

    def test_oversized_job_is_failed_without_loading_the_models(self):
        self.client.job['input']['files']['logs']['bytes'] = 2 * 1024 * 1024
        processor = Mock()
        self.assertEqual(self.run_job(processor=processor)['status'], 'failed')
        processor.assert_not_called()
        self.assertEqual(len(self.client.failures), 1)
        self.assertIsNone(self.client.finished)

    def test_lost_lease_never_commits_or_marks_a_new_attempt_failed(self):
        outcome = self.run_job(processor=Mock(side_effect=LeaseLost('expired')))
        self.assertEqual(outcome['status'], 'lease_lost')
        self.assertFalse(self.client.failures)
        self.assertIsNone(self.client.finished)

    def test_exception_text_is_not_saved_or_shown_and_resources_are_released(self):
        secret = SECRETS['OPSFUSION_SUPABASE_SECRET_KEY']
        outcome = self.run_job(processor=Mock(side_effect=ValueError(secret)))
        self.assertEqual(outcome['status'], 'failed')
        self.assertNotIn(secret, str(outcome) + str(self.client.failures))
        self.assertTrue(self.client.closed)
        self.assertFalse(self.runtime.lock.locked())


class ProcessorPageTests(unittest.TestCase):
    def page(self, configured=True):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_file(str(ROOT / 'cloud_processor/app.py'))
        if configured:
            app.secrets.update(SECRETS)
        return app

    def test_no_secrets_or_wrong_code_never_contacts_cloud(self):
        with patch('opsfusion.on_demand.run_next') as run:
            missing = self.page(configured=False).run()
            self.assertFalse(missing.exception)
            self.assertFalse(missing.button)
            app = self.page().run()
            app.text_input[0].set_value('wrong')
            app.button[0].click().run()
            self.assertTrue(app.error)
            self.assertFalse(any(b.label == 'Process next queued job' for b in app.button))
            run.assert_not_called()

    def test_unlock_does_not_claim_until_click_and_lock_removes_access(self):
        with patch('opsfusion.on_demand.run_next', return_value={
                'status': 'succeeded', 'job_id': 'fixture-job', 'observations': 1, 'incidents': 1}) as run:
            app = self.page().run()
            app.text_input[0].set_value(CODE)
            app.button[0].click().run()
            self.assertFalse(app.exception)
            run.assert_not_called()
            next(b for b in app.button if b.label == 'Process next queued job').click().run()
            run.assert_called_once()
            self.assertTrue(any('Completed job fixture-job' in s.value for s in app.success))
            next(b for b in app.button if b.label == 'Lock processor').click().run()
            self.assertFalse(any(b.label == 'Process next queued job' for b in app.button))
            self.assertTrue(app.text_input)

    def test_invalid_setting_names_are_visible_but_values_are_not(self):
        with patch('opsfusion.on_demand.run_next') as run:
            app = self.page()
            app.secrets['OPSFUSION_PROCESSOR_ACCESS_CODE'] = 'private_fixture_marker'
            app.run()
            self.assertFalse(app.exception)
            rendered = ' '.join(item.value for item in app.info)
            self.assertIn('OPSFUSION_PROCESSOR_ACCESS_CODE', rendered)
            self.assertNotIn('private_fixture_marker', rendered)
            self.assertFalse(app.button)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
