"""Local dashboard: python -m streamlit run dashboard.py --server.address 127.0.0.1"""
import hashlib
import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from opsfusion import __version__
from opsfusion.runtime import data_root
from opsfusion import app_service as service
from opsfusion.core import parse_time
from opsfusion.raw_dashboard import RAW_SOURCE, render_inputs, render_generated_evidence
from opsfusion import raw_ingest
from opsfusion.live_dashboard import LIVE_SOURCE, render_live
from opsfusion.history import History
from opsfusion.auth_dashboard import require_login, authorize, can

CODE_ROOT = Path(__file__).resolve().parent
ROOT = data_root(CODE_ROOT)
DEMOS = {'Synthetic incident demo': 'incident-demo', 'Synthetic segmentation regression': 'segmentation-regression'}

st.set_page_config(page_title='OpsFusion | Incident review', page_icon='🔎', layout='wide')
st.title('OpsFusion')
st.caption(f'Incident review workspace · Step 16 · v{__version__} · authenticated incident review')
identity = require_login(ROOT)

with st.sidebar:
    st.header('Inputs')
    source_choices = [RAW_SOURCE, *DEMOS, 'Upload adapter events', LIVE_SOURCE] if can(ROOT,'analyse') else [LIVE_SOURCE]
    source = st.selectbox('Observation source', source_choices, key='source')
    event_file = label_file = None
    if source == 'Upload adapter events':
        event_file = st.file_uploader('Adapter observations (.jsonl)', type=['jsonl'], key='events_upload')
        label_file = st.file_uploader('Independent labels (optional .json)', type=['json'], key='labels_upload')
        st.caption('Use combined_events.jsonl from your Step 4 run. Up to 200 observations; benchmarks up to 100. JSON limit: 2 MiB.')
    if source == LIVE_SOURCE:
        engine, window, gap, run_clicked = 'episode', 90, 20, False
        st.caption('Camera controls are in the main panel. Live sessions use episode fusion with a 90-second horizon and 20-second quiet gap.')
    else:
        engine = st.selectbox('Fusion engine', ['episode', 'fixed'], key='engine')
        window = st.number_input('Correlation horizon (seconds)', min_value=1.0, max_value=3600.0, value=90.0, key='window')
        gap = st.number_input('Quiet gap (seconds)', min_value=1.0, max_value=3600.0, value=20.0, disabled=engine == 'fixed', key='gap')
        run_clicked = st.button('Run replay', type='primary', use_container_width=True, key='run', disabled=source == RAW_SOURCE)
    if source == RAW_SOURCE:
        st.caption('Upload and analyse recordings in the main panel. Detectors produce observations before replay.')
    st.caption('A quiet gap means no qualifying adapter observations. It does not verify physical recovery.')
    st.divider()
    st.caption('Explanations use the offline catalogue. No model calls or automatic actions. Optional live model selection remains available through the Step 7 CLI.')

if source == LIVE_SOURCE:
    render_live(ROOT)
    st.stop()

if source == RAW_SOURCE:
    raw_events = raw_labels = None
    fingerprint = render_inputs(ROOT, engine, window, gap)
elif source in DEMOS:
    folder = CODE_ROOT / 'data' / DEMOS[source]
    raw_events = (folder / 'events.jsonl').read_bytes()
    raw_labels = (folder / 'labels.json').read_bytes()
else:
    raw_events = event_file.getvalue() if event_file else None
    raw_labels = label_file.getvalue() if label_file else None

if source != RAW_SOURCE:
    fingerprint = hashlib.sha256((raw_events or b'') + b'\0' + (raw_labels or b'') +
                                json.dumps([source, engine, window, gap]).encode()).hexdigest()
if run_clicked or ('bundle' not in st.session_state and source in DEMOS):
    try:
        if raw_events is None:
            raise ValueError('Upload your adapter event JSONL before running replay')
        events = service.load_event_upload(raw_events)
        labels = service.decode_json(raw_labels) if raw_labels is not None else None
        if labels is not None:
            service.validate_label_shape(labels)
        with st.spinner('Replaying observations and assembling cited explanations…'):
            bundle = service.run_events([e.to_dict() for e in events], engine, window, gap)
        st.session_state.update(bundle=bundle, labels=labels, source_name=source,
                                fingerprint=fingerprint, benchmark=None, raw_run=None, raw_download=None)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        st.error(str(exc))

if 'bundle' not in st.session_state:
    st.info('Upload recordings and choose Analyse recordings and fuse incidents to start.' if source == RAW_SOURCE else 'Upload adapter observations and choose Run replay to start.')
    st.stop()

bundle = st.session_state.bundle
result = bundle['replay']
events = bundle['events']
raw_run = st.session_state.get('raw_run')
st.caption(f"Showing: {st.session_state.source_name} · {result['configuration']['engine']} engine · run {result['run_id']}")
if st.session_state.fingerprint != fingerprint:
    st.warning('Inputs changed. Run replay to apply them. The workspace still shows the previous successful run.')

provenance = bundle['provenance']
if provenance['synthetic_observations']:
    st.warning(f"Synthetic provenance: {provenance['synthetic_observations']} of {len(events)} observations. Demo scores measure fusion on supplied events; they do not measure real-world perception accuracy.")
else:
    st.info('Observation provenance: no synthetic flags on this run’s observations. Source/model provenance is adapter supplied and has not been independently verified.')
st.caption('Causes remain unconfirmed. Risk is rule priority, not a calibrated probability or safety rating. Recommendations never execute.')
if raw_run is not None:
    st.success('This run was computed from uploaded raw recordings and records, using the existing perception adapters.')
    st.caption('Outputs saved locally: ' + raw_run['root'])
    with st.expander('Raw-input sources, adapter counts and model provenance'):
        st.json({'sources': raw_run['sources'], 'adapters': raw_run['adapters'], 'models': raw_run['models']})
    if 'video' in raw_run['adapters']:
        video_info = raw_run['adapters']['video']
        st.write(f"Video analysed: {video_info['sampled_frames']} sampled frames · {video_info['entry_observations']} confirmed entries · {video_info['initial_presence_observations']} initial-presence observations")
        path = raw_ingest.artifact_path(raw_run, 'video/annotated.mp4')
        st.download_button('Download annotated detector video', path.read_bytes(), 'annotated.mp4', 'video/mp4', key='raw_annotated_video')

cols = st.columns(4)
for column, name, value in zip(cols, ['Observations', 'Accepted observations', 'First alerts', 'Final incident reports'],
                               [len(events), result['final_report']['accepted_events'], len(result['first_alerts']), len(result['final_report']['incidents'])]):
    column.metric(name, value)

tabs = st.tabs(['Timeline', 'Incident evidence', 'Explanations', 'Benchmarks', 'Downloads'])
with tabs[0]:
    st.subheader('Observation timeline')
    st.caption('Virtual event time, sorted into equal-time batches. This is a replay, not a live feed or transport-latency measurement.')
    st.caption('The time cursor filters this tab. Incident and explanation tabs review the complete run with their own evidence-scope selector.')
    assets = sorted({e['asset_id'] for e in events})
    if st.session_state.get('asset', 'All assets') not in ['All assets', *assets]:
        st.session_state['asset'] = 'All assets'
    asset = st.selectbox('Asset', ['All assets', *assets], key='asset')
    times = sorted({e['timestamp'] for e in events}, key=parse_time)
    if len(times) > 1:
        if st.session_state.get('cursor', times[-1]) not in times:
            st.session_state['cursor'] = times[-1]
        cursor = st.select_slider('Replay through event time (UTC)', options=times, value=times[-1], key='cursor')
    elif times:
        cursor = times[0]
        st.caption('Replay time: ' + cursor)
    else:
        cursor = None
    shown_events = [e for e in events if (asset == 'All assets' or e['asset_id'] == asset) and
                    (cursor is None or parse_time(e['timestamp']) <= parse_time(cursor))]
    shown_alerts = [a for a in result['first_alerts'] if (asset == 'All assets' or a['asset_id'] == asset) and
                    (cursor is None or parse_time(a['first_emitted_at']) <= parse_time(cursor))]
    if shown_events:
        frame = pd.DataFrame(shown_events)
        # Vega's temporal encoding consumes validated ISO strings. Avoid pandas'
        # single-format inference, which rejects mixed whole/fractional seconds.
        frame['time'] = frame['timestamp']
        chart = alt.Chart(frame).mark_circle(size=90).encode(
            x=alt.X('time:T', title='Event time (UTC)'), y=alt.Y('asset_id:N', title='Asset'),
            color=alt.Color('modality:N', title='Source modality'),
            tooltip=['timestamp', 'event_id', 'asset_id', 'kind', 'confidence', 'synthetic']).properties(height=max(220, min(480, len(assets) * 35)))
        st.altair_chart(chart, use_container_width=True)
        st.dataframe(frame[['timestamp', 'asset_id', 'modality', 'kind', 'confidence', 'synthetic', 'event_id']], hide_index=True, use_container_width=True)
    else:
        st.info('No observations in this view.')
    st.write(f'{len(shown_alerts)} first alerts available through this time')
    st.dataframe([{'first_emitted_at': a['first_emitted_at'], 'asset_id': a['asset_id'], 'type': a['type'],
                   'risk': a['snapshot']['risk'], 'synthetic': a['synthetic'], 'incident_id': a['snapshot']['incident_id']}
                  for a in shown_alerts], hide_index=True, use_container_width=True)

with tabs[1]:
    st.subheader('Incident evidence')
    scope = st.radio('Evidence scope', ['first', 'final'], horizontal=True, key='scope',
                     help='First freezes the event records available when an alert was emitted. Final includes later accepted context.')
    incidents = [a['snapshot'] for a in result['first_alerts']] if scope == 'first' else result['final_report']['incidents']
    by_id = {i['incident_id']: i for i in incidents}
    if by_id:
        if st.session_state.get('incident') not in by_id:
            st.session_state['incident'] = next(iter(by_id))
        incident_id = st.selectbox('Incident', list(by_id), key='incident',
                                   format_func=lambda ident: f"{by_id[ident]['asset_id']} · {by_id[ident]['type'].replace('_', ' ')} · {ident}")
        incident = by_id[incident_id]
        st.write(f"{incident['type'].replace('_', ' ').title()} · {incident['asset_id']} · {incident['risk']} rule priority")
        st.caption(f"{scope.capitalize()} scope · {incident['start']} to {incident['end']} · cause unconfirmed")
        st.dataframe(incident['timeline'], hide_index=True, use_container_width=True)
        st.write('Trigger event IDs')
        st.code(', '.join(incident['trigger_event_ids']), language=None)
        with st.expander('Evidence references and source reports'):
            for e in incident['timeline']:
                st.text(f"{e['event_id']} · {e['source_id']}\n{e['summary']}\nReference: {e['evidence_ref']}")
        if raw_run is not None:
            render_generated_evidence(raw_run, incident)
        st.caption('References are metadata. Upload a matching local file below to review it. File identity is not automatically verified; batch evidence may contain later context even in first scope.')
        media = st.file_uploader('Review a local evidence file', type=['png', 'jpg', 'jpeg', 'mp4', 'wav'], key='media')
        if media is not None:
            st.caption('Manually supplied evidence: ' + media.name)
            suffix = Path(media.name).suffix.lower()
            try:
                if media.size > 25 * 1024 * 1024:
                    raise ValueError('Evidence preview limit is 25 MiB')
                if suffix in ('.png', '.jpg', '.jpeg'):
                    st.image(media.getvalue(), use_container_width=True)
                elif suffix == '.wav':
                    st.audio(media.getvalue(), format='audio/wav')
                else:
                    st.video(media.getvalue(), format='video/mp4')
            except (ValueError, OSError):
                st.error('This evidence file could not be previewed. Choose a supported media file.')
    else:
        incident_id = None
        st.info('No incident reports. Empty or low-confidence inputs can legitimately produce no alerts.')

with tabs[2]:
    st.subheader('Cited possibilities and checks')
    st.caption('Offline deterministic catalogue · zero live model calls · structural citation checks do not validate physical causes.')
    if by_id:
        reports = bundle['explanations'] if scope == 'first' else service.explanations(result, 'final')
        report = next(r for r in reports if r['incident_id'] == incident_id)
        st.write(f"Selected incident: {incident_id} · {scope} scope")
        st.info('Cause status: unconfirmed. Every check is a recommendation only.')
        st.write('Possible explanations')
        for possibility in report['possible_explanations']:
            st.write(possibility['statement'])
            st.caption('Cited event IDs: ' + ', '.join(possibility['evidence_ids']))
        st.write('Recommended checks')
        for check in report['recommended_checks']:
            st.write(check['instruction'])
            st.caption('Cited event IDs: ' + ', '.join(check['evidence_ids']))
        with st.expander('Source observations, uncertainty and validation details'):
            st.json(report)
        st.download_button('Download selected explanation', service.json_bytes(report), 'selected_explanation.json', 'application/json', key='download_selected')
    else:
        st.info('No incident to explain.')

with tabs[3]:
    st.subheader('Compare fusion behavior')
    st.caption('Fixed versus episode engines use the same observations and independent labels. Nine modality and clock ablations use the fixed engine. Non-target report categories are recorded separately, not counted as labelled faults.')
    labels = st.session_state.labels
    if labels is None:
        st.info('Upload independent labels with your events to enable evaluation. Metrics cannot be inferred from generated incident reports.')
    else:
        st.text('Label scope: ' + labels['scope'])
        if len(events) > service.MAX_BENCHMARK_EVENTS:
            st.info('Benchmark limit is 100 observations. Use the existing CLI for larger corpora.')
        elif st.button('Run benchmark comparison', key='benchmark_run'):
            try:
                with st.spinner('Evaluating both engines and nine fixed-engine variants…'):
                    st.session_state.benchmark = service.benchmark_events(events, labels,
                        bundle['review_settings']['window_seconds'], bundle['review_settings']['quiet_gap_seconds'])
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                st.error('Labels cannot score this run: ' + str(exc))
    benchmark = st.session_state.benchmark
    if benchmark is not None:
        st.dataframe(benchmark['comparison'], hide_index=True, use_container_width=True)
        st.dataframe(benchmark['ablations'], hide_index=True, use_container_width=True)
        st.caption(benchmark['note'])
        with st.expander('Matches, missed labels and ignored categories'):
            st.json(benchmark['metrics'])

with tabs[4]:
    if st.button('Save this run to incident history', key='save_history'):
        try:
            authorize(ROOT,'review')
            session_id = History(ROOT/'runs/history.sqlite3').import_bundle(bundle)
            st.success('Run saved. Select Live monitoring & history in the sidebar to review it. Session: '+session_id)
            st.toast('Run saved to incident history.')
        except (ValueError, OSError, PermissionError) as exc:
            st.error(str(exc))
    st.subheader('Export this run')
    st.caption('The bundle includes original observations, replay snapshots, final reports and first-scope explanations. Media files are not bundled. Current benchmark results are included if computed.')
    st.download_button('Download review bundle (.zip)', service.export_run(bundle, st.session_state.benchmark),
                       'opsfusion-review.zip', 'application/zip', type='primary', key='download_bundle')
    st.download_button('Replay JSON', service.json_bytes(result), 'replay.json', 'application/json', key='download_replay')
    st.download_button('Final incidents JSON', service.json_bytes(result['final_report']), 'incidents.json', 'application/json', key='download_incidents')
    st.download_button('Replay HTML report', service.render_report(result), 'replay_report.html', 'text/html', key='download_html')
    if st.session_state.benchmark is not None:
        st.download_button('Comparison CSV', service.csv_bytes(st.session_state.benchmark['comparison']),
                           'comparison.csv', 'text/csv', key='download_comparison')
    if raw_run is not None:
        st.write('Source evidence and adapter output files')
        st.caption('The source ZIP includes generated evidence, raw score tables, manifests and newly trained models. Original video/audio uploads are optional; the review ZIP above remains separate.')
        include_media = st.checkbox('Include original uploaded video/audio in the source ZIP', key='raw_export_media')
        if st.button('Prepare source evidence ZIP', key='raw_prepare_export'):
            try:
                with st.spinner('Packaging source evidence…'):
                    st.session_state.raw_download = (raw_run['root'], include_media, raw_ingest.export_artifacts(raw_run, include_media))
            except (ValueError, OSError) as exc:
                st.error(str(exc))
        archive = st.session_state.get('raw_download')
        if archive and archive[:2] == (raw_run['root'], include_media):
            st.download_button('Download source evidence ZIP', archive[2], 'opsfusion-source-evidence.zip', 'application/zip', key='raw_download_export')
