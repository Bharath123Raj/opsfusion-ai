"""Bounded, stateless operations shared by the local dashboard and HTTP API."""
import csv
import io
import json
import math
import zipfile
from dataclasses import fields, replace
from datetime import timedelta

from .core import Event
from .replay import replay, render_report
from .reasoning import packet, explain, render_reports
from .incident_eval import evaluate
from .benchmark import VARIANTS

MAX_BYTES = 2 * 1024 * 1024
MAX_EVENTS = 200
MAX_BENCHMARK_EVENTS = 100
STRING_LIMITS = {'event_id': 256, 'timestamp': 64, 'asset_id': 256,
                 'modality': 32, 'kind': 64, 'source_id': 256,
                 'evidence_ref': 2048, 'summary': 4000}
EVENT_FIELDS = {f.name for f in fields(Event)}


def json_bytes(value):
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False).encode('utf-8')


def decode_json(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('File exceeds the 2 MiB JSON limit')
    try:
        return json.loads(raw.decode('utf-8-sig'), parse_constant=lambda s: (_ for _ in ()).throw(ValueError('Nonfinite JSON number')))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Expected UTF-8 JSON') from exc


def validate_events(rows, limit=MAX_EVENTS):
    if not isinstance(rows, list) or len(rows) > limit:
        raise ValueError(f'Expected a list of at most {limit} observations')
    events = []
    for number, row in enumerate(rows, 1):
        try:
            if not isinstance(row, dict) or set(row) != EVENT_FIELDS:
                raise ValueError('Use exactly the fields in the event contract')
            for key, maximum in STRING_LIMITS.items():
                if not isinstance(row[key], str) or not row[key].strip() or len(row[key]) > maximum:
                    raise ValueError(f'{key} must be nonempty text of at most {maximum} characters')
            events.append(Event(**row))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError(f'Observation {number}: {exc}') from exc
    # Validate duplicate identities even when observations will be filtered later.
    seen = {}
    for event in events:
        if event.event_id in seen and seen[event.event_id] != event:
            raise ValueError(f'Conflicting event ID: {event.event_id}')
        seen[event.event_id] = event
    return sorted(seen.values(), key=lambda e: (e.time, e.event_id))


def load_event_upload(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('File exceeds the 2 MiB JSON limit')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeError as exc:
        raise ValueError('Expected UTF-8 event JSONL') from exc
    rows = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(decode_json(line.encode('utf-8')))
        except ValueError as exc:
            raise ValueError(f'Line {line_number}: {exc}') from exc
        if len(rows) > MAX_EVENTS:
            raise ValueError(f'Upload limit is {MAX_EVENTS} observations; select a smaller capture')
    return validate_events(rows)


def validate_settings(engine, window_seconds, quiet_gap_seconds):
    if engine not in ('fixed', 'episode'):
        raise ValueError('Engine must be fixed or episode')
    for name, value in [('window_seconds', window_seconds), ('quiet_gap_seconds', quiet_gap_seconds)]:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 3600:
            raise ValueError(f'{name} must be finite, positive and at most 3600')


def explanations(result, scope='first'):
    if scope not in ('first', 'final'):
        raise ValueError('Scope must be first or final')
    if scope == 'first':
        return [explain(packet(a['snapshot'], scope='first', emitted_at=a['first_emitted_at']))
                for a in result['first_alerts']]
    return [explain(packet(i, scope='final')) for i in result['final_report']['incidents']]


def run_events(rows, engine='episode', window_seconds=90, quiet_gap_seconds=20):
    validate_settings(engine, window_seconds, quiet_gap_seconds)
    # Canonical integral settings prevent UI/API float coercion changing fixed-engine IDs.
    window_seconds = int(window_seconds) if float(window_seconds).is_integer() else window_seconds
    quiet_gap_seconds = int(quiet_gap_seconds) if float(quiet_gap_seconds).is_integer() else quiet_gap_seconds
    events = validate_events(rows)
    result = replay(events, engine=engine, window_seconds=window_seconds, quiet_gap_seconds=quiet_gap_seconds)
    return {'schema': 'opsfusion_app_run_v1', 'events': [e.to_dict() for e in events],
            'replay': result, 'explanations': explanations(result),
            'review_settings': {'engine': engine, 'window_seconds': window_seconds, 'quiet_gap_seconds': quiet_gap_seconds},
            'provenance': {'synthetic_observations': sum(e.synthetic for e in events),
                           'other_observations': sum(not e.synthetic for e in events),
                           'note': 'Provenance is supplied by the adapters; non-synthetic does not mean independently verified.'},
            'explanation_mode': 'offline_deterministic_catalogue', 'live_model_calls': 0}


def benchmark_events(rows, labels, window_seconds=90, quiet_gap_seconds=20):
    validate_settings('episode', window_seconds, quiet_gap_seconds)
    events = validate_events(rows, limit=MAX_BENCHMARK_EVENTS)
    validate_label_shape(labels)
    results, comparisons, ablations = {}, [], []
    metric_keys = ['tp', 'fp', 'fn', 'precision', 'recall', 'f1',
                   'matched_detection_delay_median_seconds', 'false_alerts_per_normal_asset_hour']
    for engine in ('fixed', 'episode'):
        r = replay(events, engine=engine, window_seconds=window_seconds, quiet_gap_seconds=quiet_gap_seconds)
        m = evaluate(r, labels)
        results[engine] = m
        comparisons.append({'engine': engine, 'observations': len(events), **{k: m[k] for k in metric_keys}})
    for name, modalities in VARIANTS.items():
        chosen = events if modalities is None else [e for e in events if e.modality in modalities]
        if name == 'audio_clock_plus30s':
            chosen = [replace(e, timestamp=(e.time + timedelta(seconds=30)).isoformat())
                      if e.modality == 'audio' else e for e in chosen]
        r = replay(chosen, engine='fixed', window_seconds=window_seconds)
        m = evaluate(r, labels)
        results['ablation:' + name] = m
        ablations.append({'variant': name, 'observations': len(chosen), **{k: m[k] for k in metric_keys}})
    return {'schema': 'opsfusion_app_benchmark_v1', 'scope': labels['scope'], 'synthetic': labels['synthetic'],
            'configuration': {'window_seconds': window_seconds, 'quiet_gap_seconds': quiet_gap_seconds},
            'comparison': comparisons, 'ablations': ablations, 'metrics': results,
            'note': 'Adapter-event fusion evaluation only. Ablations use the fixed engine. The +30s case changes audio event clocks, not network arrival delays. Known demos are development fixtures, not blind validation.'}


def validate_label_shape(labels):
    # Bound the matching problem and reject malformed nested types before evaluation.
    required = {'schema', 'scope', 'synthetic', 'evaluation_types', 'incidents', 'coverage'}
    if not isinstance(labels, dict) or not required <= labels.keys():
        raise ValueError('Labels must contain schema, scope, synthetic, evaluation_types, incidents and coverage')
    if type(labels['synthetic']) is not bool or not isinstance(labels['scope'], str) or not 0 < len(labels['scope']) <= 4000:
        raise ValueError('Invalid label provenance or scope')
    types = labels['evaluation_types']
    if not isinstance(types, list) or not 1 <= len(types) <= 20 or any(not isinstance(t, str) or len(t) > 128 for t in types):
        raise ValueError('Invalid evaluation_types')
    for name, text_fields in [('incidents', ['label_id', 'asset_id', 'type', 'start', 'end']),
                              ('coverage', ['asset_id', 'start', 'end'])]:
        rows = labels[name]
        if not isinstance(rows, list) or len(rows) > 500:
            raise ValueError(f'{name} must be a list of at most 500 intervals')
        for row in rows:
            if not isinstance(row, dict) or any(not isinstance(row.get(f), str) or not 0 < len(row[f]) <= 256 for f in text_fields):
                raise ValueError(f'Invalid {name} interval')
            if name == 'coverage' and type(row.get('normal')) is not bool:
                raise ValueError('Coverage needs a boolean normal flag')


def csv_bytes(rows):
    output = io.StringIO(newline='')
    if rows:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return output.getvalue().encode('utf-8')


def export_run(bundle, benchmark=None):
    result = bundle['replay']
    files = {'events.jsonl': b''.join(json.dumps(e, ensure_ascii=False).encode('utf-8') + b'\n' for e in bundle['events']),
             'replay.json': json_bytes(result), 'incidents.json': json_bytes(result['final_report']),
             'explanations.json': json_bytes(bundle['explanations']),
             'replay_report.html': render_report(result).encode('utf-8'),
             'explanations_report.html': render_reports(bundle['explanations']).encode('utf-8'),
             'run_manifest.json': json_bytes({'schema': bundle['schema'], 'run_id': result['run_id'],
                 'configuration': result['configuration'], 'provenance': bundle['provenance'],
                 'review_settings': bundle['review_settings'],
                 'explanation_scope': 'first', 'explanation_mode': bundle['explanation_mode'], 'live_model_calls': 0})}
    if benchmark is not None:
        files.update({'benchmark.json': json_bytes(benchmark),
                      'comparison.csv': csv_bytes(benchmark['comparison']),
                      'ablation.csv': csv_bytes(benchmark['ablations'])})
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return output.getvalue()
