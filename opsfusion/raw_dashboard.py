"""Raw-data controls for the local Streamlit app. No silent demo-data substitution."""
from dataclasses import fields
import hashlib
import json
from pathlib import Path
import uuid
import streamlit as st
from . import raw_ingest as raw
from . import app_service as service
from .auth_dashboard import authorize, can

RAW_SOURCE = 'Real recordings'
DEFAULT_POLYGON = '[[0.45, 0.35], [0.9, 0.35], [0.9, 0.95], [0.45, 0.95]]'


def upload(widget):
    return raw.UploadedInput(widget.name, widget.getvalue()) if widget is not None else None


def fingerprint(request, settings, labels):
    values = {}
    for field in fields(request):
        value = getattr(request, field.name)
        if isinstance(value, raw.UploadedInput):
            value = {'name': value.name, 'sha256': hashlib.sha256(value.data).hexdigest()}
        elif isinstance(value, Path):
            value = str(value)
        values[field.name] = value
    values.update(settings=settings, labels_sha256=hashlib.sha256(labels).hexdigest() if labels else None)
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def render_inputs(root, engine, window, gap):
    st.subheader('Analyse real recordings')
    st.write('Upload recorded video, machine measurements, machine audio or structured controller logs. OpsFusion runs the detectors, then opens the resulting incidents in the review workspace below.')
    cols = st.columns(4)
    video_on = cols[0].checkbox('Video', value=True, key='raw_video_on')
    sensor_on = cols[1].checkbox('Sensor CSV', key='raw_sensor_on')
    audio_on = cols[2].checkbox('Audio WAV', key='raw_audio_on')
    logs_on = cols[3].checkbox('Controller logs', key='raw_logs_on')
    r = raw.RawInputs()
    if video_on or audio_on:
        r.asset_id = st.text_input('Video/audio asset ID', value='warehouse-zone-01', key='raw_asset')
        st.caption('Use the same physical asset ID as its sensor and log CSV records. Recording starts must be actual timezone-aware times; assigning a common demo time does not synchronize sources.')
    if video_on:
        with st.expander('Video · person detection and zone tracking', expanded=True):
            r.video = upload(st.file_uploader('Camera recording', type=['mp4', 'avi', 'mov', 'mkv'], key='raw_video'))
            r.video_start = st.text_input('Video recording start', placeholder='2026-10-13T09:00:00+05:30', key='raw_video_start')
            r.camera_id = st.text_input('Camera ID', value='camera-01', key='raw_camera')
            polygon_text = st.text_area('Zone polygon · normalized [x, y] vertices', value=DEFAULT_POLYGON, key='raw_polygon')
            st.caption('Example coordinates are shown. Set your actual ground zone and preview it on the first frame. x/y run from 0 to 1 across the image; person foot position determines zone membership.')
            try:
                r.polygon = json.loads(polygon_text)
            except json.JSONDecodeError:
                st.error('Zone polygon must be a JSON array of coordinate pairs.')
            r.max_video_seconds = st.number_input('Analyse first N video seconds', min_value=1.0, max_value=60.0, value=20.0, key='raw_video_seconds')
            r.sample_fps = st.number_input('Video sample FPS', min_value=1.0, max_value=10.0, value=5.0, key='raw_video_fps')
            r.video_synthetic = st.checkbox('This video is generated/composed', key='raw_video_synthetic')
            st.caption('CPU YOLOv8n person inference. First use may download official model weights. Recorded-file analysis, not a live camera connection. Upload limit: 100 MiB.')
            preview_token = hashlib.sha256((r.video.data if r.video else b'') + polygon_text.encode()).hexdigest()
            if st.button('Preview zone on first frame', key='raw_preview'):
                try:
                    if r.video is None:
                        raise ValueError('Upload a camera recording first')
                    picture, metadata = raw.preview_video(r.video, r.polygon)
                    st.session_state.raw_preview_data = (preview_token, picture, metadata)
                except (ValueError, RuntimeError, OSError, TypeError) as exc:
                    st.error(str(exc))
            preview = st.session_state.get('raw_preview_data')
            if preview and preview[0] == preview_token:
                st.image(preview[1], caption='First frame with the configured zone', use_container_width=True)
                st.json(preview[2], expanded=False)
    if sensor_on:
        with st.expander('Sensors · vibration and temperature anomaly detection', expanded=True):
            r.sensor = upload(st.file_uploader('Sensor measurements to analyse', type=['csv'], key='raw_sensor'))
            st.caption('CSV columns: timestamp, asset_id, vibration_rms_mm_s, temperature_c, synthetic. Timestamps require a timezone. Upload limit: 5 MiB / 20,000 rows.')
            methods=['Train from normal files', 'Use a trusted local model'] if can(root,'model_admin') else ['Train from normal files']
            method = st.radio('Sensor baseline', methods, key='raw_sensor_method')
            if method == 'Train from normal files':
                r.sensor_train = upload(st.file_uploader('Normal sensor training CSV', type=['csv'], key='raw_sensor_train'))
                r.sensor_validation = upload(st.file_uploader('Normal sensor calibration CSV', type=['csv'], key='raw_sensor_validation'))
                st.caption('At least 120 training and 80 calibration rows per asset. Train < calibration < analysed measurements. Baseline assets must match capture assets.')
            else:
                text = st.text_input('Trusted sensor model path', placeholder=r'C:\...\models\sensor-v1\sensor.joblib', key='raw_sensor_model')
                r.sensor_model = Path(text).expanduser() if text.strip() else None
    if audio_on:
        with st.expander('Audio · deviation from normal machine sound', expanded=True):
            r.audio = upload(st.file_uploader('Machine audio to analyse', type=['wav'], key='raw_audio'))
            r.audio_start = st.text_input('Audio recording start', placeholder='2026-10-13T09:00:00+05:30', key='raw_audio_start')
            r.audio_synthetic = st.checkbox('This analysed audio is generated/composed', key='raw_audio_synthetic')
            st.caption('16-kHz mono PCM16 or float WAV; no silent resampling or channel conversion. Upload limit: 20 MiB.')
            methods=['Train from normal files', 'Use a trusted local model'] if can(root,'model_admin') else ['Train from normal files']
            method = st.radio('Audio baseline', methods, key='raw_audio_method')
            if method == 'Train from normal files':
                r.audio_train = upload(st.file_uploader('Normal audio training WAV', type=['wav'], key='raw_audio_train'))
                r.audio_train_start = st.text_input('Normal training recording start', key='raw_audio_train_start')
                r.audio_validation = upload(st.file_uploader('Normal audio calibration WAV', type=['wav'], key='raw_audio_validation'))
                r.audio_validation_start = st.text_input('Normal calibration recording start', key='raw_audio_validation_start')
                r.audio_baseline_synthetic = st.checkbox('These normal baseline recordings are generated/composed', key='raw_audio_baseline_synthetic')
                st.caption('At least 24 training seconds and 12 calibration seconds. Separate nonoverlapping clips: train < calibration < analysed audio, with different content hashes.')
            else:
                text = st.text_input('Trusted audio model path', placeholder=r'C:\...\models\audio-v1\audio.pt', key='raw_audio_model')
                r.audio_model = Path(text).expanduser() if text.strip() else None
    if logs_on:
        with st.expander('Controller logs · structured event mapping', expanded=True):
            r.logs = upload(st.file_uploader('Controller log CSV', type=['csv'], key='raw_logs'))
            st.caption('Columns: timestamp, asset_id, source_id, level, code, message, synthetic. Known codes such as MOTOR_OVERLOAD, TEMP_WARNING and PROTECTIVE_SHUTDOWN are mapped; unknown codes remain unmapped. Upload limit: 5 MiB / 20,000 rows.')
    if sensor_on or audio_on:
        r.normal_periods_reviewed = st.checkbox('I reviewed the uploaded training/calibration periods as known normal', key='raw_normal_reviewed')
        if can(root,'model_admin'):
            r.trust_local_models = st.checkbox('Any local model path points to a model I trained or otherwise trust', key='raw_model_trusted')
        st.caption('Models trained on generated data propagate synthetic provenance, even when scoring a real recording. Never use a demo baseline as proof of real machine fault accuracy.')
    label_file = st.file_uploader('Independent incident labels (optional)', type=['json'], key='raw_labels')
    label_bytes = label_file.getvalue() if label_file else None
    token = fingerprint(r, [engine, window, gap], label_bytes)
    if st.button('Analyse recordings and fuse incidents', type='primary', key='raw_analyse'):
        folder = root / 'runs' / 'raw-ingestion' / uuid.uuid4().hex
        try:
            authorize(root,'analyse')
            if r.sensor_model is not None or r.audio_model is not None:
                authorize(root,'model_admin')
            for name, selected in [('video', video_on), ('sensor', sensor_on), ('audio', audio_on), ('logs', logs_on)]:
                if selected and getattr(r, name) is None:
                    raise ValueError(f'Upload the selected {name} input, or turn that source off')
            labels = service.decode_json(label_bytes) if label_bytes else None
            if labels is not None:
                service.validate_label_shape(labels)
            progress = st.empty()
            with st.spinner('Running raw-data adapters…'):
                record = raw.run_inputs(r, folder, engine, window, gap, progress=progress.info)
                bundle = service.run_events(record['events'], engine, window, gap)
            st.session_state.update(bundle=bundle, labels=labels, source_name=RAW_SOURCE, fingerprint=token,
                                    benchmark=None, raw_run=record, raw_download=None)
            progress.success('Raw recordings analysed. Review the resulting timeline, evidence and explanations below.')
        except (ValueError, RuntimeError, OSError, KeyError, TypeError, ImportError, PermissionError) as exc:
            st.error(str(exc))
            if folder.exists():
                st.caption(f'Incomplete adapter files were retained at {folder}. They were not loaded as a successful run.')
    return token


def render_generated_evidence(record, incident):
    available = {e['event_id']: record['evidence'][e['event_id']] for e in incident['timeline'] if e['event_id'] in record['evidence']}
    if not available:
        return
    st.write('Evidence generated from these uploaded recordings')
    if st.session_state.get('raw_evidence_event') not in available:
        st.session_state.raw_evidence_event = next(iter(available))
    event_id = st.selectbox('Source observation evidence', list(available), key='raw_evidence_event')
    entry = available[event_id]
    try:
        if entry.get('media'):
            path = raw.artifact_path(record, entry['media'])
            if path.suffix == '.jpg':
                st.image(path.read_bytes(), caption=event_id, use_container_width=True)
            elif path.suffix == '.wav':
                st.audio(path.read_bytes(), format='audio/wav')
        path = raw.artifact_path(record, entry['record'])
        if path.suffix == '.json':
            with st.expander('Adapter evidence records and scores'):
                st.json(json.loads(path.read_text(encoding='utf-8')))
        st.download_button('Download generated evidence record', path.read_bytes(), path.name, key='raw_evidence_download')
    except (ValueError, OSError) as exc:
        st.error(str(exc))
