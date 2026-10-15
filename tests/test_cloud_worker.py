import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

import httpx

from opsfusion.cloud_worker import CloudClient, LeaseLost, configuration, inputs_for, process_job
from test_raw_ingest import controller, video_fixture, ScriptedDetector


def job_for(files, settings=None):
    return {'id': str(uuid.uuid4()), 'created_by': str(uuid.uuid4()), 'lease_token': str(uuid.uuid4()),
            'asset_id': 'machine-01', 'camera_id': 'camera-01', 'input': {'version': 1, 'settings': settings or {},
            'files': {slot: {'name': slot + Path(upload.name).suffix.lower(), 'bytes': len(upload.data),
                             'sha256': hashlib.sha256(upload.data).hexdigest()} for slot, upload in files.items()}}}


class FakeCloud:
    def __init__(self, job, files):
        self.job = job
        self.files = {job['created_by'] + '/' + job['id'] + '/' + job['input']['files'][slot]['name']: upload.data for slot, upload in files.items()}
        self.uploads = {}; self.finished = None; self.valid = True

    def download(self, path, maximum):
        return self.files[path]

    def upload(self, path, data, content_type):
        if path in self.uploads: raise AssertionError('Artifact overwrite')
        self.uploads[path] = data

    def rpc(self, name, body):
        if name == 'opsfusion_heartbeat': return self.valid
        if name == 'opsfusion_worker_ping': return None
        if name == 'opsfusion_finish_job': self.finished = body; return self.valid
        raise AssertionError(name)


class CloudWorkerTests(unittest.TestCase):
    def test_logs_process_to_frozen_reports_and_private_bundle(self):
        files = {'logs': controller()}; job = job_for(files); client = FakeCloud(job, files)
        with tempfile.TemporaryDirectory() as td:
            result = process_job(job, client, td, str(uuid.uuid4()), 'test')
        self.assertEqual((result['observations'], result['incidents']), (1, 1))
        self.assertEqual(client.finished['p_lease_token'], job['lease_token'])
        incident = client.finished['p_incidents'][0]
        self.assertEqual(incident['current_report']['hypothesis']['status'], 'unconfirmed')
        self.assertFalse(incident['current_report']['synthetic'])
        self.assertEqual(incident['first_report']['incident_id'], incident['incident_id'])
        prefix = job['id'] + '/' + job['lease_token'] + '/'
        self.assertTrue(all(p.startswith(prefix) for p in client.uploads))
        review = next(a for a in result['artifacts'] if a['name'] == 'review.zip')
        with zipfile.ZipFile(io.BytesIO(client.uploads[review['path']])) as archive:
            self.assertIn('incidents.json', archive.namelist())
        self.assertTrue(all(path in client.uploads for refs in result['evidence'].values() for path in refs.values()))

    def test_decoded_video_produces_downloadable_evidence(self):
        files = {'video': video_fixture()}
        job = job_for(files, {'video_start': '2026-01-01T10:00:00Z', 'polygon': [[.5,.1],[.9,.1],[.9,.95],[.5,.95]]})
        client = FakeCloud(job, files)
        with tempfile.TemporaryDirectory() as td:
            result = process_job(job, client, td, str(uuid.uuid4()), 'test', detector=ScriptedDetector())
        self.assertEqual(result['observations'], 1)
        self.assertTrue(client.finished['p_incidents'][0]['current_report']['synthetic'])
        self.assertTrue(any(a['name'] == 'video/annotated.mp4' for a in result['artifacts']))
        self.assertTrue(any(path.endswith('.jpg') for paths in result['evidence'].values() for path in paths.values()))

    def test_input_path_model_checksum_and_size_guards(self):
        files = {'logs': controller()}; job = job_for(files); client = FakeCloud(job, files)
        for mutation in [lambda j: j['input']['files']['logs'].update(name='../logs.csv'),
                         lambda j: j['input']['files']['logs'].update(sha256='0'*64),
                         lambda j: j['input']['files']['logs'].update(bytes=0),
                         lambda j: j['input']['settings'].update(sensor_model='/tmp/model.joblib'),
                         lambda j: j['input']['files'].update(sensor_model={})]:
            changed = copy.deepcopy(job); mutation(changed)
            with self.assertRaises(ValueError): inputs_for(changed, client)

    def test_lost_lease_does_not_commit(self):
        files = {'logs': controller()}; job = job_for(files); client = FakeCloud(job, files); client.valid = False
        with tempfile.TemporaryDirectory() as td, self.assertRaises(LeaseLost):
            process_job(job, client, td, str(uuid.uuid4()), 'test')
        self.assertIsNone(client.finished); self.assertFalse(client.uploads)

    def test_modern_secret_key_is_not_sent_as_jwt(self):
        seen = []
        def handler(request):
            seen.append(request); return httpx.Response(200, json=True)
        client = CloudClient('https://example.supabase.co', 'sb_secret_' + 'x'*32, transport=httpx.MockTransport(handler))
        try: client.rpc('opsfusion_heartbeat', {})
        finally: client.close()
        self.assertIn('apikey', seen[0].headers)
        self.assertNotIn('authorization', seen[0].headers)

    def test_frontend_key_and_non_hosted_url_rejected(self):
        for url, key in [('https://example.supabase.co', 'sb_publishable_public'), ('http://127.0.0.1', 'sb_secret_'+'x'*32)]:
            with patch.dict(os.environ, {'OPSFUSION_SUPABASE_URL': url, 'OPSFUSION_SUPABASE_SECRET_KEY': key}), self.assertRaises(ValueError): configuration()


if __name__ == '__main__': unittest.main()
