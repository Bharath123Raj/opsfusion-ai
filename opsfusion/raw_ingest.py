"""Local uploaded recordings -> existing adapters -> canonical observation files.

This module is deliberately separate from the stateless event-only HTTP API.
Model paths are explicit trusted-local inputs; serialized model uploads are not accepted.
"""
from dataclasses import dataclass
import csv
import hashlib
import io
import json
import math
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from urllib.request import url2pathname
import zipfile

from .core import parse_time
from .__main__ import load_events, write_json
from .vision_logic import validate_polygon
from .app_service import validate_events, validate_settings

MIB = 1024 * 1024


@dataclass(frozen=True)
class UploadedInput:
    name: str
    data: bytes


@dataclass
class RawInputs:
    video: UploadedInput | None = None
    sensor: UploadedInput | None = None
    audio: UploadedInput | None = None
    logs: UploadedInput | None = None
    asset_id: str = 'machine-01'
    camera_id: str = 'camera-01'
    video_start: str = ''
    audio_start: str = ''
    polygon: list | None = None
    video_synthetic: bool = False
    audio_synthetic: bool = False
    max_video_seconds: float = 20
    sample_fps: float = 5
    # Trained locally from the user's uploaded known-normal periods, or explicit trusted files.
    sensor_train: UploadedInput | None = None
    sensor_validation: UploadedInput | None = None
    sensor_model: Path | None = None
    audio_train: UploadedInput | None = None
    audio_validation: UploadedInput | None = None
    audio_train_start: str = ''
    audio_validation_start: str = ''
    audio_baseline_synthetic: bool = False
    audio_model: Path | None = None
    normal_periods_reviewed: bool = False
    trust_local_models: bool = False


def check_file(upload, suffixes, maximum, csv_rows=None):
    if not isinstance(upload, UploadedInput) or not isinstance(upload.data, bytes) or not upload.data:
        raise ValueError('Select a nonempty input file')
    if Path(upload.name).suffix.lower() not in suffixes:
        raise ValueError(f'Expected file extension: {", ".join(suffixes)}')
    if len(upload.data) > maximum:
        raise ValueError(f'{upload.name}: file exceeds {maximum // MIB} MiB limit')
    if csv_rows is not None:
        try:
            reader = csv.reader(io.StringIO(upload.data.decode('utf-8-sig')))
            next(reader, None)
            count = sum(1 for row in reader if row)
        except (UnicodeError, csv.Error) as exc:
            raise ValueError('CSV must be valid UTF-8') from exc
        if count > csv_rows:
            raise ValueError(f'CSV limit is {csv_rows} data rows for interactive analysis')


def validate_inputs(r):
    if not any((r.video, r.sensor, r.audio, r.logs)):
        raise ValueError('Upload at least one raw video, sensor CSV, WAV or log CSV')
    for name in ('asset_id', 'camera_id'):
        value = getattr(r, name)
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError(f'{name} must be nonempty text of at most 256 characters')
    for name in ('video_synthetic', 'audio_synthetic', 'audio_baseline_synthetic', 'normal_periods_reviewed', 'trust_local_models'):
        if type(getattr(r, name)) is not bool:
            raise ValueError(f'{name} must be a boolean')
    if r.video:
        check_file(r.video, ('.mp4', '.avi', '.mov', '.mkv'), 100 * MIB)
        parse_time(r.video_start)
        validate_polygon(r.polygon)
        for value, maximum, name in [(r.max_video_seconds, 60, 'Video duration'), (r.sample_fps, 10, 'Sample FPS')]:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError(f'{name} must be positive and at most {maximum}')
        if r.sample_fps < 1:
            raise ValueError('Sample FPS must be at least 1 for the configured tracker age')
    if r.logs:
        check_file(r.logs, ('.csv',), 5 * MIB, 20000)
    for modality in ('sensor', 'audio'):
        capture = getattr(r, modality)
        if capture is None:
            continue
        suffixes, maximum = (('.csv',), 5 * MIB) if modality == 'sensor' else (('.wav',), 20 * MIB)
        check_file(capture, suffixes, maximum, 20000 if modality == 'sensor' else None)
        training, validation, model = (getattr(r, modality + '_' + part) for part in ('train', 'validation', 'model'))
        if model is not None:
            if training is not None or validation is not None:
                raise ValueError(f'{modality}: choose uploaded normal periods OR a trusted local model')
            if not r.trust_local_models:
                raise ValueError('Confirm that local models were trained by you or are otherwise trusted')
            if not Path(model).is_file():
                raise ValueError(f'{modality} model file not found: {model}')
        else:
            if training is None or validation is None:
                raise ValueError(f'{modality}: provide normal training and calibration files, or a trusted local model')
            if not r.normal_periods_reviewed:
                raise ValueError('Review and confirm that training/calibration periods are known normal')
            check_file(training, suffixes, maximum, 20000 if modality == 'sensor' else None)
            check_file(validation, suffixes, maximum, 20000 if modality == 'sensor' else None)
        if modality == 'audio':
            parse_time(r.audio_start)
            if model is None:
                parse_time(r.audio_train_start)
                parse_time(r.audio_validation_start)


def preview_video(upload, polygon):
    """Decode only a first-frame preview. No detector, observations or model calls."""
    import tempfile
    from .video import dependencies
    check_file(upload, ('.mp4', '.avi', '.mov', '.mkv'), 100 * MIB)
    validate_polygon(polygon)
    cv2, np = dependencies()
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / ('preview' + Path(upload.name).suffix.lower())
        path.write_bytes(upload.data)
        cap = cv2.VideoCapture(str(path))
        try:
            ok, frame = cap.read()
            if not ok:
                raise ValueError('Could not decode the first video frame')
            fps = float(cap.get(cv2.CAP_PROP_FPS))
            count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            h, w = frame.shape[:2]
            cv2.polylines(frame, [np.array([(round(x * w), round(y * h)) for x, y in polygon], np.int32)], True, (0, 165, 255), 3)
            ok, encoded = cv2.imencode('.jpg', frame)
            if not ok:
                raise ValueError('Could not encode video preview')
            return encoded.tobytes(), {'source_fps': fps, 'frames': count, 'width': w, 'height': h,
                                       'duration_seconds': count / fps if fps > 0 else None}
        finally:
            cap.release()


def run_inputs(r, output, engine='episode', window_seconds=90, quiet_gap_seconds=20, *, detector=None, progress=None):
    """Execute adapters, without importing demo data or generating substitute observations."""
    validate_inputs(r)
    validate_settings(engine, window_seconds, quiet_gap_seconds)
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Raw run folder already exists; choose a new run')
    output.mkdir(parents=True, mode=0o700)
    (output / 'inputs').mkdir()
    sources, events, models = {}, [], {}

    def stage(upload, stem):
        path = output / 'inputs' / (stem + Path(upload.name).suffix.lower())
        path.write_bytes(upload.data)
        sources[stem] = {'original_name': upload.name, 'staged_path': path.relative_to(output).as_posix(),
                         'bytes': len(upload.data), 'sha256': hashlib.sha256(upload.data).hexdigest()}
        return path

    def tick(message):
        if progress:
            progress(message)

    def audio_manifest(stem, upload, start, synthetic):
        clip = stage(upload, stem)
        manifest = output / 'inputs' / (stem + '.json')
        write_json(manifest, {'schema': 'opsfusion_audio_manifest_v1', 'clips': [
            {'path': clip.name, 'asset_id': r.asset_id, 'start_time': parse_time(start).isoformat(), 'synthetic': synthetic}]})
        return manifest

    try:
        if r.sensor:
            from . import sensor
            model = Path(r.sensor_model).resolve() if r.sensor_model is not None else None
            if model is None:
                tick('Training sensor baseline on uploaded normal periods')
                sensor.train(SimpleNamespace(train=stage(r.sensor_train, 'sensor_train'), validation=stage(r.sensor_validation, 'sensor_validation'),
                    output=output / 'models' / 'sensor', quantile=.995, seed=42))
                model = output / 'models' / 'sensor' / 'sensor.joblib'
            models['sensor'] = str(model)
            tick('Scoring uploaded sensor measurements')
            sensor.infer(SimpleNamespace(csv=stage(r.sensor, 'sensor_capture'), model=model, output=output / 'sensor',
                persistence=3, hard_z=6, soft_z=3, max_gap_factor=2, labels=None, merge_events=None))
            events.extend(load_events(output / 'sensor' / 'sensor_events.jsonl'))
        if r.audio:
            from . import audio
            model = Path(r.audio_model).resolve() if r.audio_model is not None else None
            if model is None:
                tick('Training audio baseline on uploaded normal recordings')
                train = audio_manifest('audio_train', r.audio_train, r.audio_train_start, r.audio_baseline_synthetic)
                validation = audio_manifest('audio_validation', r.audio_validation, r.audio_validation_start, r.audio_baseline_synthetic)
                audio.train_command(SimpleNamespace(train=train, validation=validation, output=output / 'models' / 'audio', seed=42, epochs=200, quantile=.99))
                model = output / 'models' / 'audio' / 'audio.pt'
            models['audio'] = str(model)
            tick('Scoring uploaded machine audio')
            audio.infer_command(SimpleNamespace(manifest=audio_manifest('audio_capture', r.audio, r.audio_start, r.audio_synthetic),
                model=model, output=output / 'audio', persistence=2))
            events.extend(load_events(output / 'audio' / 'audio_events.jsonl'))
        if r.logs:
            from .logs import parse_logs
            tick('Mapping structured controller records')
            records, metadata = parse_logs(stage(r.logs, 'controller_logs'))
            (output / 'logs').mkdir()
            (output / 'logs' / 'log_events.jsonl').write_text(''.join(json.dumps(e.to_dict()) + '\n' for e in records), encoding='utf-8')
            write_json(output / 'logs' / 'run_manifest.json', {'status': 'completed', **metadata})
            events.extend(records)
        if r.video:
            from .video import run
            tick('Running YOLO person detection and zone tracking on uploaded video')
            zone = output / 'inputs' / 'zone.json'
            write_json(zone, {'asset_id': r.asset_id, 'camera_id': r.camera_id, 'polygon': r.polygon,
                              'confirm_samples': 2, 'max_age_seconds': 1.0, 'min_iou': .15})
            run(SimpleNamespace(video=stage(r.video, 'camera_capture'), zone=zone, start_time=r.video_start,
                output=output / 'video', sample_fps=r.sample_fps, conf=.6, max_seconds=r.max_video_seconds,
                merge_events=None, model='yolov8n.pt', device='cpu', synthetic_video=r.video_synthetic), detector=detector)
            events.extend(load_events(output / 'video' / 'video_events.jsonl'))
        raw_rows = [e.to_dict() for e in events]
        (output / 'combined_events.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in raw_rows), encoding='utf-8')
        # Keep complete adapter outputs even if interactive review exceeds its bound.
        rows = [e.to_dict() for e in validate_events(raw_rows)]
        manifests = {name: json.loads((output / name / 'run_manifest.json').read_text(encoding='utf-8'))
                     for name in ('sensor', 'audio', 'video', 'logs') if (output / name / 'run_manifest.json').is_file()}
        evidence = {}
        for e in events:
            u = urlsplit(e.evidence_ref)
            if u.scheme != 'file' or u.netloc not in ('', 'localhost'):
                continue
            path = Path(url2pathname(u.path)).resolve()
            try:
                relative = path.relative_to(output).as_posix()
            except ValueError:
                continue
            evidence[e.event_id] = {'record': relative}
            if path.suffix == '.jpg':
                evidence[e.event_id]['media'] = relative
            elif e.modality == 'audio' and path.with_suffix('.wav').is_file():
                evidence[e.event_id]['media'] = path.with_suffix('.wav').relative_to(output).as_posix()
        record = {'schema': 'opsfusion_raw_run_v1', 'status': 'completed', 'root': str(output), 'events': rows,
                  'sources': sources, 'models': models, 'adapters': manifests, 'evidence': evidence,
                  'configuration': {'engine': engine, 'window_seconds': window_seconds, 'quiet_gap_seconds': quiet_gap_seconds},
                  'recording_mode': 'uploaded_files', 'timestamps': 'User recording starts and CSV event timestamps; clocks are not automatically synchronized.'}
        write_json(output / 'raw_run_manifest.json', record)
        return record
    except Exception as exc:
        write_json(output / 'raw_run_manifest.json', {'status': 'failed', 'outputs_are_incomplete': True, 'error': str(exc), 'sources': sources})
        raise


def artifact_path(record, relative):
    root = Path(record['root']).resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError('Artifact is outside this raw run') from exc
    if not path.is_file():
        raise ValueError('Raw run artifact is missing')
    return path


def export_artifacts(record, include_original_media=False):
    """Package generated evidence, scores and inputs; original large media is opt-in."""
    root = Path(record['root']).resolve()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        for path in sorted(root.rglob('*')):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            safe = artifact_path(record, relative)
            if not include_original_media and relative.startswith('inputs/') and path.suffix.lower() in ('.mp4', '.avi', '.mov', '.mkv', '.wav'):
                continue
            z.write(safe, relative)
    return output.getvalue()
