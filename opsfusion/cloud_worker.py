"""Supabase-backed recording jobs using the existing OpsFusion adapters.

Run on a trusted processing host. The secret key stays in that host's environment;
the Netlify frontend uses a separate publishable key and user authentication.
"""
import argparse
import base64
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import signal
import tempfile
import threading
from urllib.parse import quote, urlsplit
import uuid

import httpx

from .app_service import export_run, run_events
from .raw_ingest import RawInputs, UploadedInput, export_artifacts, run_inputs, validate_inputs

MIB = 1024 * 1024
SLOTS = {
    'video': ({'.mp4', '.avi', '.mov', '.mkv'}, 50 * MIB),
    'sensor': ({'.csv'}, 5 * MIB), 'sensor_train': ({'.csv'}, 5 * MIB),
    'sensor_validation': ({'.csv'}, 5 * MIB), 'logs': ({'.csv'}, 5 * MIB),
    'audio': ({'.wav'}, 20 * MIB), 'audio_train': ({'.wav'}, 20 * MIB),
    'audio_validation': ({'.wav'}, 20 * MIB),
}
SETTINGS = {'video_start', 'audio_start', 'audio_train_start', 'audio_validation_start',
            'polygon', 'video_synthetic', 'audio_synthetic', 'audio_baseline_synthetic',
            'normal_periods_reviewed', 'max_video_seconds', 'sample_fps'}
ARTIFACTS = {'.json', '.jsonl', '.csv', '.jpg', '.png', '.mp4', '.wav', '.zip', '.html'}


class CloudError(RuntimeError):
    pass


class LeaseLost(CloudError):
    pass


def identifier(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('Expected a canonical UUID')
    return value


def configuration(*, url=None, key=None):
    url = (os.environ.get('OPSFUSION_SUPABASE_URL', '') if url is None else url).rstrip('/')
    key = os.environ.get('OPSFUSION_SUPABASE_SECRET_KEY', '') if key is None else key
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.supabase.co')
            or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.port):
        raise ValueError('Set OPSFUSION_SUPABASE_URL to your hosted Supabase project HTTPS URL')
    if not (key.startswith('sb_secret_') and len(key) >= 30):
        # Legacy support is explicit and rejects a frontend anon key.
        try:
            payload = key.split('.')[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
            if not isinstance(claims, dict) or claims.get('role') != 'service_role': raise ValueError()
        except (ValueError, IndexError, TypeError, KeyError):
            raise ValueError('Set a backend Supabase secret key; never use the frontend publishable key') from None
    return url, key


class CloudClient:
    def __init__(self, url, key, transport=None):
        headers = {'apikey': key, 'User-Agent': 'OpsFusionWorker/0.16'}
        if not key.startswith('sb_secret_'): headers['Authorization'] = 'Bearer ' + key
        self.http = httpx.Client(base_url=url, headers=headers, timeout=30, follow_redirects=False, transport=transport)

    def close(self):
        self.http.close()

    def rpc(self, name, body):
        response = self.http.post('/rest/v1/rpc/' + name, json=body)
        if not response.is_success: raise CloudError('Cloud API rejected ' + name + ' (HTTP ' + str(response.status_code) + ')')
        return response.json() if response.content else None

    def download(self, path, maximum):
        with self.http.stream('GET', '/storage/v1/object/authenticated/opsfusion-inputs/' + quote(path, safe='/')) as response:
            if not response.is_success: raise CloudError('Private input download failed (HTTP ' + str(response.status_code) + ')')
            chunks = []; size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > maximum: raise ValueError('An input file exceeds its size limit')
                chunks.append(chunk)
        return b''.join(chunks)

    def upload(self, path, data, content_type):
        response = self.http.post('/storage/v1/object/opsfusion-results/' + quote(path, safe='/'),
                                  content=data, headers={'Content-Type': content_type, 'x-upsert': 'false'})
        if not response.is_success: raise CloudError('Private result upload failed (HTTP ' + str(response.status_code) + ')')


def inputs_for(job, client):
    job_id = identifier(job['id']); actor = identifier(job['created_by'])
    spec = job['input']
    if not isinstance(spec, dict) or set(spec) != {'version', 'files', 'settings'} or type(spec['version']) is not int or spec['version'] != 1:
        raise ValueError('Unsupported recording job format')
    files = spec['files']; settings = spec['settings']
    if not isinstance(files, dict) or not files or set(files) - set(SLOTS): raise ValueError('Unsupported file selection')
    if not isinstance(settings, dict) or set(settings) - SETTINGS: raise ValueError('Unsupported recording settings')
    if not any(slot in files for slot in ('video', 'sensor', 'audio', 'logs')): raise ValueError('Choose at least one recording source')
    for modality in ('sensor', 'audio'):
        if any(x in files for x in (modality + '_train', modality + '_validation')) and modality not in files:
            raise ValueError('Baseline files require the corresponding capture file')
    uploads = {}; total = 0
    for slot, item in files.items():
        if not isinstance(item, dict) or set(item) != {'name', 'bytes', 'sha256'}: raise ValueError('Invalid file metadata')
        name = item['name']; suffixes, maximum = SLOTS[slot]
        if not isinstance(name, str) or Path(name).suffix.lower() not in suffixes or name != slot + Path(name).suffix.lower():
            raise ValueError('File names must use their declared slot and extension')
        if type(item['bytes']) is not int or not 0 < item['bytes'] <= maximum: raise ValueError('Invalid input size')
        if not isinstance(item['sha256'], str) or len(item['sha256']) != 64: raise ValueError('Invalid input checksum')
        total += item['bytes']
        if total > 150 * MIB: raise ValueError('Job inputs exceed 150 MiB')
        raw = client.download(actor + '/' + job_id + '/' + name, maximum)
        if len(raw) != item['bytes'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('Input checksum mismatch; submit a new job')
        uploads[slot] = UploadedInput(name, raw)
    result = RawInputs(asset_id=job['asset_id'], camera_id=job['camera_id'], **settings, **uploads)
    validate_inputs(result)
    return result


class Lease:
    def __init__(self, client, job, worker_id, label):
        self.client = client; self.job = job; self.worker_id = worker_id; self.label = label
        self.stopped = threading.Event(); self.lost = threading.Event(); self.message = 'Preparing inputs'
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def beat(self, message=None):
        if message: self.message = message
        if self.lost.is_set(): raise LeaseLost('Worker lease lost')
        valid = self.client.rpc('opsfusion_heartbeat', {'p_job_id': self.job['id'],
            'p_lease_token': self.job['lease_token'], 'p_progress': self.message})
        if not valid:
            self.lost.set(); raise LeaseLost('Worker lease lost')
        self.client.rpc('opsfusion_worker_ping', {'p_worker_id': self.worker_id, 'p_label': self.label})

    def _loop(self):
        while not self.stopped.wait(30):
            try: self.beat()
            except Exception: self.lost.set(); return

    def __enter__(self):
        self.beat(); self.thread.start(); return self

    def __exit__(self, *args):
        self.stopped.set(); self.thread.join(timeout=35)


def persist_run(job, record, output, client, progress):
    bundle = run_events(record['events'], **record['configuration'])
    (output / 'review.zip').write_bytes(export_run(bundle))
    (output / 'source-evidence.zip').write_bytes(export_artifacts(record))
    (output / 'incidents.json').write_text(json.dumps(bundle['replay']['final_report'], indent=2))
    (output / 'explanations.json').write_text(json.dumps(bundle['explanations'], indent=2))
    prefix = identifier(job['id']) + '/' + identifier(job['lease_token']) + '/'
    inventory = []; total = 0
    evidence_paths = {relative for refs in record['evidence'].values() for relative in refs.values()}
    for path in sorted(output.rglob('*')):
        relative = path.relative_to(output).as_posix()
        if (not path.is_file() or path.is_symlink() or path.suffix not in ARTIFACTS
                or (relative.startswith(('inputs/', 'models/')) and relative not in evidence_paths)):
            continue
        if path.stat().st_size > 50 * MIB: raise ValueError('An output exceeds the 50 MiB cloud file limit')
        total += path.stat().st_size
        if total > 200 * MIB or len(inventory) >= 500: raise ValueError('Outputs exceed the cloud job limit')
        progress('Saving analysis artifacts')
        data = path.read_bytes(); object_path = prefix + relative
        content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        client.upload(object_path, data, content_type)
        inventory.append({'name': relative, 'path': object_path, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    first = {x['snapshot']['incident_id']: x['snapshot'] for x in bundle['replay']['first_alerts']}
    incidents = [{'incident_id': x['incident_id'], 'first_report': first[x['incident_id']], 'current_report': x}
                 for x in bundle['replay']['final_report']['incidents']]
    evidence = {event_id: {kind: prefix + relative for kind, relative in refs.items()}
                for event_id, refs in record['evidence'].items()}
    result = {'schema': 'opsfusion_cloud_result_v1', 'run_id': bundle['replay']['run_id'],
              'observations': len(record['events']), 'incidents': len(incidents), 'artifacts': inventory,
              'evidence': evidence, 'provenance': bundle['provenance'], 'explanation_mode': bundle['explanation_mode']}
    return result, incidents


def process_job(job, client, work_root, worker_id, label, *, detector=None):
    identifier(job['id']); identifier(job['lease_token'])
    with Lease(client, job, worker_id, label) as lease:
        inputs = inputs_for(job, client)
        with tempfile.TemporaryDirectory(prefix='opsfusion-job-', dir=work_root) as td:
            output = Path(td) / 'run'
            record = run_inputs(inputs, output, detector=detector, progress=lease.beat)
            result, incidents = persist_run(job, record, output, client, lease.beat)
            lease.beat('Committing incident reports')
            if not client.rpc('opsfusion_finish_job', {'p_job_id': job['id'], 'p_lease_token': job['lease_token'],
                    'p_result': result, 'p_incidents': incidents}): raise LeaseLost('Worker lease lost before commit')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='Claim at most one job, then exit')
    parser.add_argument('--poll-seconds', type=float, default=10)
    args = parser.parse_args()
    try:
        if not 2 <= args.poll_seconds <= 60: raise ValueError('Poll interval must be 2–60 seconds')
        url, key = configuration()
        root = Path(os.environ.get('OPSFUSION_WORKER_DATA', 'runs/cloud-worker')).resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        identity = root / 'worker-id'
        if not identity.exists(): identity.write_text(str(uuid.uuid4()))
        worker_id = identifier(identity.read_text().strip())
        label = os.environ.get('OPSFUSION_WORKER_LABEL', 'processing-worker')[:64] or 'processing-worker'
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM): signal.signal(sig, lambda *_: stop.set())
        client = CloudClient(url, key)
        print('OpsFusion cloud worker ready; waiting for recording jobs. No credentials printed.', flush=True)
        try:
            while not stop.is_set():
                client.rpc('opsfusion_worker_ping', {'p_worker_id': worker_id, 'p_label': label})
                rows = client.rpc('opsfusion_claim_job', {'p_worker_id': worker_id})
                if rows:
                    job = rows[0]
                    try:
                        result = process_job(job, client, root, worker_id, label)
                        print('Completed ' + job['id'] + ': ' + str(result['incidents']) + ' incidents', flush=True)
                    except LeaseLost:
                        print('Job lease expired; stale results were not committed.', flush=True)
                    except Exception as exc:
                        message = str(exc)[:1000] if isinstance(exc, ValueError) else 'Analysis failed; inspect the trusted worker environment and input formats'
                        client.rpc('opsfusion_fail_job', {'p_job_id': job['id'], 'p_lease_token': job['lease_token'], 'p_error': message})
                        print('Job failed: ' + job['id'] + ' (' + type(exc).__name__ + ')', flush=True)
                if args.once: break
                stop.wait(args.poll_seconds)
        finally: client.close()
    except (ValueError, CloudError, httpx.HTTPError) as exc:
        parser.exit(1, 'Worker stopped: ' + (str(exc) if isinstance(exc, (ValueError, CloudError)) else 'Cloud network request failed') + '\n')


if __name__ == '__main__': main()
