"""Access-controlled, one-job-per-click use of the existing cloud processor."""
from dataclasses import dataclass, field
import hashlib
import hmac
from pathlib import Path
import re
import tempfile
import threading
import uuid

from .cloud_worker import CloudClient, LeaseLost, MIB, configuration, identifier, process_job


@dataclass(frozen=True)
class Settings:
    url: str
    key: str = field(repr=False)
    access_digest: bytes = field(repr=False)
    session_stamp: str = field(repr=False)


def settings_for(secrets):
    names = ('OPSFUSION_SUPABASE_URL', 'OPSFUSION_SUPABASE_SECRET_KEY', 'OPSFUSION_PROCESSOR_ACCESS_CODE')
    if any(not isinstance(secrets.get(name), str) for name in names):
        raise ValueError('Configure the three processor secrets in the hosting settings')
    url, key = configuration(url=secrets[names[0]], key=secrets[names[1]])
    code = secrets[names[2]]
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', code):
        raise ValueError('Use a randomly generated processor access code of 32–128 URL-safe characters')
    if hmac.compare_digest(code, key):
        raise ValueError('The processor access code must be separate from the backend API key')
    digest = hashlib.sha256(code.encode()).digest()
    stamp = hmac.new(key.encode(), url.encode() + b'\0' + digest, hashlib.sha256).hexdigest()
    return Settings(url, key, digest, stamp)


def authorized(code, settings):
    return isinstance(code, str) and hmac.compare_digest(
        hashlib.sha256(code.encode()).digest(), settings.access_digest)


class ResourceLimit(ValueError):
    pass


def check_limits(job):
    """Keep the free demo smaller than the independently hosted worker."""
    files = job['input']['files']
    if sum(item['bytes'] for item in files.values()) > 40 * MIB:
        raise ResourceLimit('This free processor accepts up to 40 MiB of inputs per job')
    for slot, item in files.items():
        maximum = 20 * MIB if slot == 'video' else (MIB if slot in ('logs', 'sensor', 'sensor_train', 'sensor_validation') else 5 * MIB)
        if item['bytes'] > maximum:
            raise ResourceLimit('Free processor limits: video 20 MiB, CSV 1 MiB each, WAV 5 MiB each')
    settings = job['input']['settings']
    if 'video' in files and (not 0 < float(settings.get('max_video_seconds', 20)) <= 20
                             or not 1 <= float(settings.get('sample_fps', 5)) <= 5):
        raise ResourceLimit('For this free processor, choose up to 20 seconds of video at 1–5 FPS')


class Runtime:
    def __init__(self):
        self.lock = threading.Lock()
        self.worker_id = str(uuid.uuid4())


def run_next(settings, session_stamp, runtime, *, client_factory=CloudClient, processor=process_job):
    # Check authorization before creating a privileged client or making requests.
    if not isinstance(session_stamp, str) or not hmac.compare_digest(session_stamp.encode(), settings.session_stamp.encode()):
        raise PermissionError('Unlock the processor first')
    if not runtime.lock.acquire(blocking=False):
        return {'status': 'busy', 'message': 'Another job is running. Wait for it to finish.'}
    client = None
    try:
        client = client_factory(settings.url, settings.key)
        label = 'on-demand-streamlit'
        client.rpc('opsfusion_worker_ping', {'p_worker_id': runtime.worker_id, 'p_label': label})
        rows = client.rpc('opsfusion_claim_job', {'p_worker_id': runtime.worker_id})
        if not rows:
            return {'status': 'empty', 'message': 'No queued or reclaimable jobs. Upload a recording in the Netlify portal.'}
        job = rows[0]
        job_id = identifier(job['id'])
        lease_token = identifier(job['lease_token'])
        try:
            check_limits(job)
            with tempfile.TemporaryDirectory(prefix='opsfusion-on-demand-') as root:
                result = processor(job, client, Path(root), runtime.worker_id, label)
            return {'status': 'succeeded', 'job_id': job_id,
                    'observations': result['observations'], 'incidents': result['incidents']}
        except LeaseLost:
            return {'status': 'lease_lost', 'job_id': job_id,
                    'message': 'The lease expired. This attempt did not commit reports; a later click can reclaim it.'}
        except Exception as exc:
            # Only this locally defined limit exception has a safe, static message.
            # Never expose arbitrary exception text, credentials or raw input data.
            message = str(exc) if isinstance(exc, ResourceLimit) else 'Analysis failed. Check input formats and processor dependencies.'
            valid = client.rpc('opsfusion_fail_job', {'p_job_id': job_id, 'p_lease_token': lease_token, 'p_error': message})
            if not valid:
                return {'status': 'lease_lost', 'job_id': job_id, 'message': 'The job lease changed; this attempt could not update it.'}
            return {'status': 'failed', 'job_id': job_id, 'message': message}
    finally:
        try:
            if client is not None:
                client.close()
        finally:
            runtime.lock.release()
