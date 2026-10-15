import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('worker_setup', ROOT / 'deploy/worker.py')
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)
KEY = 'sb_secret_' + 'fixture_' * 4


class WorkerSetupTests(unittest.TestCase):
    def test_private_file_is_exclusive_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            worker.configure(root, worker.PROJECT_URL, KEY, 'oracle-worker')
            path = worker.configured(root)
            if os.name != 'nt':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                worker.configure(root, worker.PROJECT_URL, KEY + 'other', 'other')
            self.assertEqual(path.read_bytes(), before)
            path.unlink()
            target = root / 'unrelated'; target.write_text('keep')
            path.symlink_to(target)
            with self.assertRaises(FileExistsError):
                worker.configure(root, worker.PROJECT_URL, KEY, 'oracle-worker')
            with self.assertRaises(ValueError):
                worker.configured(root)
            self.assertEqual(target.read_text(), 'keep')

    def test_injection_and_frontend_keys_are_rejected_before_writing(self):
        for url, key, label in [(worker.PROJECT_URL, 'sb_publishable_' + 'x'*40, 'worker'),
                                (worker.PROJECT_URL, KEY + '\nOTHER=value', 'worker'),
                                (worker.PROJECT_URL, KEY + '${TOKEN}', 'worker'),
                                (worker.PROJECT_URL + '/path', KEY, 'worker'),
                                ('http://127.0.0.1', KEY, 'worker'),
                                (worker.PROJECT_URL, KEY, 'worker\nOTHER=value')]:
            with self.subTest(url=url, label=label), tempfile.TemporaryDirectory() as td:
                with self.assertRaises(ValueError):
                    worker.configure(Path(td), url, key, label)
                self.assertFalse(list(Path(td).iterdir()))

    def test_compose_gets_private_file_without_key_in_arguments_or_environment(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            worker.configure(root, worker.PROJECT_URL, KEY, 'oracle-worker')
            with patch.dict(os.environ, {'OPSFUSION_SUPABASE_SECRET_KEY': 'inherited-bad',
                                         'COMPOSE_FILE': 'other.yml', 'COMPOSE_PROJECT_NAME': 'other'}), \
                    patch.object(worker.subprocess, 'run') as run:
                worker.call(root, 'up', '--detach', '--no-build')
            args, kwargs = run.call_args
            self.assertNotIn(KEY, str(args))
            self.assertNotIn('OPSFUSION_SUPABASE_SECRET_KEY', kwargs['env'])
            self.assertFalse(any(k.startswith('COMPOSE_') for k in kwargs['env']))
            self.assertIn(str(root / worker.ENV_NAME), args[0])
            self.assertIn('opsfusion-processing', args[0])
            self.assertEqual(args[0][-3:], ['up', '--detach', '--no-build'])
            self.assertTrue(kwargs['check'])
            self.assertNotIn('shell', kwargs)

    def test_modified_or_public_configuration_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            worker.configure(root, worker.PROJECT_URL, KEY, 'worker')
            path = root / worker.ENV_NAME
            if os.name != 'nt':
                path.chmod(0o644)
                with self.assertRaises(ValueError): worker.configured(root)
                path.chmod(0o600)
            path.write_text(path.read_text() + 'OTHER=value\n')
            with self.assertRaises(ValueError): worker.configured(root)

    def test_cli_rejects_noninteractive_secret_entry(self):
        # No secret on argv/stdin and no configuration created in the repository.
        result = subprocess.run([os.sys.executable, str(ROOT / 'deploy/worker.py'), 'configure'],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('interactive SSH terminal', result.stderr)


if __name__ == '__main__':
    unittest.main()
