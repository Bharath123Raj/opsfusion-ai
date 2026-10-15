"""Private configuration and Docker Compose commands for the recording worker.

Run on the processing VM. Secrets are read without echo, never accepted as CLI
arguments, and stored in a new owner-only file excluded from Git.
"""
import argparse
import getpass
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

PROJECT_URL = 'https://blvorawnwbcnujnapmgg.supabase.co'
ENV_NAME = '.env.cloud-worker'
VARIABLES = ('OPSFUSION_SUPABASE_URL', 'OPSFUSION_SUPABASE_SECRET_KEY', 'OPSFUSION_WORKER_LABEL')


def values(url, key, label):
    if not re.fullmatch(r'https://[a-z0-9]{20}\.supabase\.co', url):
        raise ValueError('Use the hosted Supabase project HTTPS URL, without a trailing slash')
    if not re.fullmatch(r'sb_secret_[A-Za-z0-9_-]{20,300}', key):
        raise ValueError('Use a backend sb_secret_ key from this project; its value will not be printed')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', label):
        raise ValueError('Worker label must be 1–64 letters, digits, underscores or hyphens')
    return dict(zip(VARIABLES, (url, key, label)))


def configure(root, url, key, label):
    settings = values(url, key, label)
    path = root / ENV_NAME
    # O_EXCL also refuses a dangling symlink; never overwrite an existing key.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as target:
        target.write(''.join(name + '=' + settings[name] + '\n' for name in VARIABLES))


def configured(root):
    path = root / ENV_NAME
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Worker configuration must be a regular file, not a symlink')
    if os.name != 'nt' and (info.st_mode & 0o077 or info.st_uid != os.getuid()):
        raise ValueError('Worker configuration must be owned by this user with mode 600')
    if info.st_size > 1024:
        raise ValueError('Worker configuration has an invalid format')
    text = path.read_text(encoding='utf-8')
    lines = text.splitlines()
    if len(lines) != len(VARIABLES):
        raise ValueError('Worker configuration has an invalid format')
    settings = {}
    for expected, line in zip(VARIABLES, lines):
        name, separator, value = line.partition('=')
        if separator != '=' or name != expected:
            raise ValueError('Worker configuration has an invalid format')
        settings[name] = value
    checked = values(*(settings[name] for name in VARIABLES))
    if text != ''.join(name + '=' + checked[name] + '\n' for name in VARIABLES):
        raise ValueError('Worker configuration has an invalid format')
    return path


def call(root, *args):
    path = configured(root)
    # Use only the validated file for worker settings and stable Compose naming.
    env = {name: value for name, value in os.environ.items()
           if name not in VARIABLES and not name.startswith('COMPOSE_')}
    command = ['docker', 'compose', '--project-name', 'opsfusion-processing',
               '--project-directory', str(root), '--env-file', str(path),
               '-f', str(root / 'compose.cloud-worker.yaml'), *args]
    return subprocess.run(command, cwd=root, env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('configure', 'build', 'start', 'status', 'logs', 'stop'))
    parser.add_argument('--url', default=PROJECT_URL, help='Public Supabase project URL for configuration')
    parser.add_argument('--label', default='oracle-worker', help='Non-secret worker label for configuration')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.action == 'configure':
            if not sys.stdin.isatty():
                raise ValueError('Run configure in an interactive SSH terminal so the secret is not echoed')
            if (root / ENV_NAME).exists() or (root / ENV_NAME).is_symlink():
                raise FileExistsError('Configuration already exists; it was not overwritten')
            key = getpass.getpass('Supabase backend secret key (hidden): ')
            configure(root, args.url, key, args.label)
            print('Private worker settings created. No credentials printed. No service started.')
        else:
            commands = {'build': ('build', '--pull'),
                        'start': ('up', '--detach', '--no-build'),
                        'status': ('ps',), 'logs': ('logs', '--tail', '60', '--no-color'),
                        'stop': ('stop', '--timeout', '120')}
            call(root, *commands[args.action])
    except (ValueError, FileExistsError, FileNotFoundError, PermissionError) as exc:
        # Avoid path/OSError representations that might include configuration data.
        message = str(exc) if isinstance(exc, ValueError) else 'Check Docker, the configuration file and its permissions; existing files were preserved'
        parser.exit(1, message + '\n')
    except subprocess.CalledProcessError:
        parser.exit(1, 'Docker command failed. Review its output above; no credentials were printed by this helper.\n')


if __name__ == '__main__':
    main()
