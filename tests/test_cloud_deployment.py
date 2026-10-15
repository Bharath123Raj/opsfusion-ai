import importlib.util
import gc
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from opsfusion.accounts import Accounts
from opsfusion.maintenance import backup
from opsfusion import maintenance
from opsfusion.upgrade import upgrade

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cloud_commands', ROOT / 'deploy/cloud.py')
cloud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cloud)


class CloudDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_domain_rejects_urls_ports_injection_and_ip(self):
        for value in ('https://a.example', 'a.example/path', 'a.example:443',
                      '127.0.0.1', 'localhost', 'a..example', '-a.example',
                      'a.example\nX=bad', 'a.$example', 'a.123', 'a.example.'):
            with self.subTest(value=value), self.assertRaises(ValueError): cloud.domain(value)
        self.assertEqual(cloud.domain(' OpsFusion-Fixture.DUCKDNS.ORG '), 'opsfusion-fixture.duckdns.org')

    def test_configure_private_and_existing_file_untouched(self):
        cloud.configure(self.root, 'fixture.duckdns.org')
        path = self.root / '.env.cloud'
        if os.name != 'nt': self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError): cloud.configure(self.root, 'other.duckdns.org')
        self.assertEqual(cloud.configured(self.root), 'fixture.duckdns.org')

    def test_configure_rejects_symlink_and_noncanonical_env(self):
        other = self.root / 'other'; other.write_text('keep')
        path = self.root / '.env.cloud'; path.symlink_to(other)
        with self.assertRaises(FileExistsError): cloud.configure(self.root, 'fixture.example')
        with self.assertRaises(ValueError): cloud.configured(self.root)
        self.assertEqual(other.read_text(), 'keep'); path.unlink()
        for value in ('OPSFUSION_DOMAIN=fixture.example\nOTHER=bad\n', 'OPSFUSION_DOMAIN=fixture.example'):
            path.write_text(value)
            with self.assertRaises(ValueError): cloud.configured(self.root)

    def test_compose_ignores_inherited_domain_and_project_override(self):
        cloud.configure(self.root, 'fixture.example')
        with patch.dict(os.environ, {'OPSFUSION_DOMAIN': 'evil.example', 'COMPOSE_PROJECT_NAME': 'other'}), patch.object(cloud.subprocess, 'run') as run:
            cloud.call(self.root, 'config', '--quiet')
        args, kwargs = run.call_args
        self.assertEqual(args[0][-2:], ['config', '--quiet'])
        self.assertIn(str(self.root / 'compose.cloud.yaml'), args[0])
        self.assertNotIn('OPSFUSION_DOMAIN', kwargs['env'])
        self.assertNotIn('COMPOSE_PROJECT_NAME', kwargs['env'])
        self.assertTrue(kwargs['check']); self.assertNotIn('shell', kwargs)

    def test_restore_running_stack_rejected_before_reading_archive(self):
        path = self.root / 'backup.zip'; path.write_bytes(b'fixture')
        with patch.object(cloud, 'call', return_value=subprocess.CompletedProcess([], 0, 'running-id\n')) as call:
            with self.assertRaises(ValueError): cloud.import_backup(self.root, path)
        self.assertEqual(call.call_count, 1)

    def test_restore_passes_bytes_on_stdin_without_host_mount_or_tty(self):
        path = self.root / 'backup.zip'; path.write_bytes(b'fixture')
        seen = []
        def execute(root, *args, **kwargs):
            if args[0] == 'ps': return subprocess.CompletedProcess([], 0, '')
            seen.append((args, kwargs['stdin'].read()))
        with patch.object(cloud, 'call', side_effect=execute): cloud.import_backup(self.root, path)
        args, data = seen[0]
        self.assertEqual(data, b'fixture'); self.assertIn('-T', args)
        self.assertNotIn('--volume', args); self.assertEqual(args[-1], cloud.IMPORT_CODE)

    def test_actual_stdin_restore_preserves_account_and_wipes_sessions(self):
        old = self.root / 'old'; volume = self.root / 'volume'; volume.mkdir()
        accounts = Accounts(old / 'runs/accounts.sqlite3')
        password = 'Cloud restore fixture password 123!'
        accounts.bootstrap('bharath', password); token = accounts.login('bharath', password)
        result = backup(old, self.root / 'backups', offline=True)
        code = cloud.IMPORT_CODE.replace("dir='/data'", 'dir=' + repr(str(volume))).replace("'/data/site'", repr(str(volume / 'site')))
        env = {**os.environ, 'PYTHONPATH': str(ROOT)}
        with open(result['archive'], 'rb') as source:
            proc = subprocess.run([sys.executable, '-c', code], stdin=source, env=env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        restored = Accounts(volume / 'site/runs/accounts.sqlite3')
        with self.assertRaises(PermissionError): restored.identity(token)
        self.assertEqual(restored.identity(restored.login('bharath', password))['username'], 'bharath')
        self.assertFalse(list(volume.glob('cloud-import-*')))
        # Populated sites survive an accidental repeat import.
        with open(result['archive'], 'rb') as source:
            retry = subprocess.run([sys.executable, '-c', code], stdin=source, env=env, capture_output=True, text=True)
        self.assertNotEqual(retry.returncode, 0)
        self.assertTrue(restored.initialized()); self.assertFalse(list(volume.glob('cloud-import-*')))

    def test_start_checks_accounts_before_gateway_start(self):
        with patch.object(cloud, 'call', side_effect=subprocess.CalledProcessError(1, ['fixture'])) as call:
            with self.assertRaises(subprocess.CalledProcessError): cloud.start(self.root)
        self.assertEqual(call.call_count, 1)
        self.assertNotIn('up', call.call_args.args)

    def test_backup_stays_valid_when_gc_runs_between_hash_and_zip_write(self):
        old = self.root / 'old'
        accounts = Accounts(old / 'runs/accounts.sqlite3')
        accounts.bootstrap('bharath', 'Cloud checkpoint fixture password 123!')
        accounts.login('bharath', 'Cloud checkpoint fixture password 123!')
        original_sha = maintenance.sha
        def collect_after_hash(path):
            digest = original_sha(path)
            gc.collect()
            return digest
        enabled = gc.isenabled(); gc.disable()
        try:
            with patch.object(maintenance, 'sha', side_effect=collect_after_hash):
                result = backup(old, self.root / 'backups', offline=True)
            maintenance.restore(result['archive'], self.root / 'restored', offline=True)
        finally:
            if enabled: gc.enable()
        self.assertTrue(Accounts(self.root / 'restored/runs/accounts.sqlite3').initialized())

    def test_step15_upgrade_preserves_private_data_and_same_version_noop(self):
        old = self.root / 'old'; new = self.root / 'new'
        for root, version in ((old, '0.15.0'), (new, '0.16.0')):
            (root / 'opsfusion').mkdir(parents=True)
            (root / 'opsfusion/__init__.py').write_text("__version__ = '" + version + "'")
            (root / 'dashboard.py').write_text(version)
        (old / 'runs').mkdir(); (old / 'runs/private.txt').write_text('preserved')
        (new / '.env.cloud').write_text('source configuration must not be copied')
        result = upgrade(old, new)
        self.assertEqual(result['to_version'], '0.16.0')
        self.assertEqual((old / 'runs/private.txt').read_text(), 'preserved')
        self.assertFalse((old / '.env.cloud').exists())
        self.assertTrue(upgrade(old, new)['already_current'])


if __name__ == '__main__': unittest.main()
