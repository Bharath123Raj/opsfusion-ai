"""Host-side cloud commands. Requires Python 3 and Docker Compose v2 on the VM.

No cloud-provider credentials, SSH keys, account passwords or DNS tokens are
stored here. The selected Compose project name is stable across code folders.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]

# Runs as the unprivileged application user; importing an archive does not load
# model pickles. Temporary bytes are bounded and removed, including on failure.
IMPORT_CODE = """
import pathlib, sys, tempfile
from opsfusion.maintenance import restore, MAX_BYTES
with tempfile.TemporaryDirectory(prefix='cloud-import-', dir='/data') as td:
    archive = pathlib.Path(td) / 'backup.zip'
    size = 0
    with archive.open('wb') as dest:
        while chunk := sys.stdin.buffer.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError('Archive exceeds the 2 GiB transfer limit')
            dest.write(chunk)
    print(restore(archive, '/data/site', offline=True))
"""


def domain(value):
    value = value.strip().lower()
    if len(value) > 253 or '.' not in value:
        raise ValueError('Use a full DNS hostname such as your-name.duckdns.org')
    labels = value.split('.')
    if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', x) for x in labels):
        raise ValueError('Use only the DNS hostname, without https://, a port or path')
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise ValueError('A DNS hostname is required for this HTTPS configuration')
    if not re.fullmatch('[a-z]{2,63}', labels[-1]):
        raise ValueError('Use a public DNS hostname')
    return value


def configure(root, value):
    root = Path(root)
    value = domain(value)
    target = root / '.env.cloud'
    # Exclusive creation also rejects a pre-existing symlink. Never overwrite.
    with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
        f.write('OPSFUSION_DOMAIN=' + value + '\n')
    return value


def configured(root):
    path = Path(root) / '.env.cloud'
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024:
        raise ValueError('Run configure once in this deployment folder')
    value = path.read_text()
    prefix = 'OPSFUSION_DOMAIN='
    if not value.startswith(prefix) or value.count('\n') != 1 or not value.endswith('\n'):
        raise ValueError('Unexpected cloud configuration; use one OPSFUSION_DOMAIN line')
    return domain(value[len(prefix):-1])


def compose(root):
    root = Path(root).resolve()
    configured(root)
    return ['docker', 'compose', '--project-directory', str(root), '--env-file',
            str(root / '.env.cloud'), '-f', str(root / 'compose.cloud.yaml')]


def call(root, *args, **kwargs):
    # Inherited domain variables must not silently override the reviewed file.
    env = dict(os.environ)
    env.pop('OPSFUSION_DOMAIN', None)
    env.pop('COMPOSE_PROJECT_NAME', None)
    env.pop('COMPOSE_PROFILES', None)
    return subprocess.run(compose(root) + list(args), check=True, env=env, **kwargs)


def offline(root):
    result = call(root, 'ps', '--status', 'running', '--quiet', capture_output=True, text=True)
    if result.stdout.strip():
        raise ValueError('Stop the cloud stack before restore or backup; close source sessions first')


def import_backup(root, archive):
    path = Path(archive).resolve()
    if not path.is_file() or not 0 < path.stat().st_size <= 2 * 1024**3:
        raise ValueError('Choose a trusted backup ZIP smaller than 2 GiB')
    offline(root)
    # No shell, host-volume sharing, world-readable file or command-line secret.
    with path.open('rb') as source:
        return call(root, 'run', '--rm', '--no-deps', '-T', 'app', 'python', '-c', IMPORT_CODE, stdin=source)


def start(root):
    # Fail before exposing the gateway if the persistent account store is empty.
    code = "from pathlib import Path; from opsfusion.runtime import data_root; from opsfusion.accounts import Accounts; p=data_root()/'runs/accounts.sqlite3'; assert p.is_file() and Accounts(p).initialized(), 'Restore an existing backup OR bootstrap a fresh cloud account before starting'"
    call(root, 'run', '--rm', '--no-deps', '-T', 'app', 'python', '-c', code)
    call(root, 'up', '-d', '--wait', '--wait-timeout', '180')


def status(root):
    call(root, 'ps')
    # A container listing alone is not an application health result.
    call(root, 'exec', '-T', 'app', 'python', '-m', 'opsfusion.service', '--healthcheck')
    print('Backend health passed. Also open https://' + configured(root) + ' to check public TLS/login.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    q = sub.add_parser('configure'); q.add_argument('--domain', required=True)
    for name in ('build', 'up', 'status', 'stop', 'logs', 'backup'):
        sub.add_parser(name)
    q = sub.add_parser('bootstrap'); q.add_argument('--username', required=True)
    q = sub.add_parser('restore'); q.add_argument('--archive', type=Path, required=True)
    q = sub.add_parser('export-backup'); q.add_argument('--name', required=True); q.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == 'configure':
            print('Configured https://' + configure(ROOT, args.domain)); return
        if args.action == 'build':
            call(ROOT, 'config', '--quiet'); call(ROOT, 'build', '--pull')
        elif args.action == 'bootstrap':
            if not re.fullmatch('[a-z0-9_.-]{3,64}', args.username):
                raise ValueError('Use a valid OpsFusion username')
            offline(ROOT)
            call(ROOT, 'run', '--rm', '--no-deps', 'app', 'python', '-m', 'opsfusion.accounts', 'bootstrap', '--username', args.username)
        elif args.action == 'restore': import_backup(ROOT, args.archive)
        elif args.action == 'up': start(ROOT)
        elif args.action == 'status': status(ROOT)
        elif args.action == 'stop': call(ROOT, 'stop')
        elif args.action == 'logs': call(ROOT, 'logs', '--tail', '80', 'app', 'gateway')
        elif args.action == 'backup':
            offline(ROOT)
            call(ROOT, 'run', '--rm', '--no-deps', '-T', 'app', 'python', '-m', 'opsfusion.maintenance', 'backup', '--project', '/data/site', '--output-dir', '/data/backups', '--offline')
        elif args.action == 'export-backup':
            if not re.fullmatch(r'opsfusion-backup-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}\.zip', args.name):
                raise ValueError('Use the exact generated backup filename, without a path')
            # Exclusive creation prevents accidental replacement of another backup.
            target = args.output.resolve()
            with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as dest:
                try:
                    call(ROOT, 'run', '--rm', '--no-deps', '-T', 'app', 'cat', '/data/backups/' + args.name, stdout=dest)
                except BaseException:
                    target.unlink(missing_ok=True); raise
            print(json.dumps({'exported': str(target)}))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, 'Cloud command failed: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
