"""Dependency-free, evidence-first fusion baseline. All times normalize to UTC."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import math

MODALITIES = {'sensor', 'video', 'audio', 'log', 'maintenance', 'note'}
KINDS = {'vibration_high', 'temperature_high', 'abnormal_noise', 'motor_warning',
         'sensor_anomaly', 'zone_entry', 'zone_presence', 'shutdown', 'recent_repair', 'operator_note'}


def parse_time(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('timestamp must include timezone')
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class Event:
    event_id: str
    timestamp: str
    asset_id: str
    modality: str
    kind: str
    source_id: str
    evidence_ref: str
    summary: str
    confidence: float
    synthetic: bool

    def __post_init__(self):
        parse_time(self.timestamp)
        if self.modality not in MODALITIES or self.kind not in KINDS:
            raise ValueError('unsupported modality or event kind')
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (float, int)) or not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError('confidence must be finite and between 0 and 1')
        if type(self.synthetic) is not bool:
            raise ValueError('synthetic must be boolean')
        for field in ('event_id', 'asset_id', 'source_id', 'evidence_ref', 'summary'):
            if not isinstance(getattr(self, field), str) or not getattr(self, field).strip():
                raise ValueError(f'{field} required')

    @property
    def time(self):
        return parse_time(self.timestamp)

    def to_dict(self):
        result = asdict(self)
        result['timestamp'] = self.time.isoformat()
        return result


def fuse(events, window_seconds=90, minimum_confidence=0.6, standalone_audio_logs=False):
    """Bounded same-asset clusters; no transitive bridging across long incidents.

    This is batch fusion. A future streaming engine must handle watermarks and
    late arrivals. Evidence quality threshold is not incident probability.
    """
    if window_seconds <= 0 or not math.isfinite(window_seconds):
        raise ValueError('window_seconds must be positive and finite')
    if not 0 <= minimum_confidence <= 1:
        raise ValueError('invalid minimum_confidence')
    by_id = {}
    for event in events:
        if event.event_id in by_id and by_id[event.event_id] != event:
            raise ValueError(f'conflicting event ID: {event.event_id}')
        by_id[event.event_id] = event
    groups = {}
    for event in sorted(by_id.values(), key=lambda e: (e.time, e.event_id)):
        if event.confidence < minimum_confidence:
            continue
        groups.setdefault(event.asset_id, []).append(event)
    incidents, graph_nodes, graph_edges = [], [], []
    for asset in sorted(groups):
        clusters = []
        for event in groups[asset]:
            if not clusters or (event.time - clusters[-1][0].time).total_seconds() > window_seconds:
                clusters.append([event])
            else:
                clusters[-1].append(event)
        for cluster in clusters:
            for event in cluster:
                graph_nodes.append(event.to_dict())
            for i, left in enumerate(cluster):
                for right in cluster[i+1:]:
                    graph_edges.append({'source': left.event_id, 'target': right.event_id,
                        'relationship': 'same_asset_temporal_cooccurrence',
                        'delta_seconds': (right.time-left.time).total_seconds()})
            kinds = {e.kind for e in cluster}
            indicators = [e for e in cluster if e.kind in {'vibration_high', 'temperature_high', 'abnormal_noise', 'motor_warning', 'sensor_anomaly'}]
            # Require two independent modalities; duplicated sensor readings do not qualify.
            if len({e.modality for e in indicators}) >= 2:
                incidents.append(build_incident(asset, 'machine_degradation', cluster, indicators))
            if standalone_audio_logs and 'abnormal_noise' in kinds:
                incidents.append(build_incident(asset, 'acoustic_anomaly', cluster, [e for e in cluster if e.kind == 'abnormal_noise']))
            if standalone_audio_logs and 'motor_warning' in kinds:
                incidents.append(build_incident(asset, 'operational_warning', cluster, [e for e in cluster if e.kind == 'motor_warning']))
            if 'sensor_anomaly' in kinds:
                incidents.append(build_incident(asset, 'sensor_anomaly', cluster, [e for e in cluster if e.kind == 'sensor_anomaly']))
            if 'zone_entry' in kinds:
                incidents.append(build_incident(asset, 'restricted_zone', cluster, [e for e in cluster if e.kind == 'zone_entry']))
            if 'zone_presence' in kinds:
                incidents.append(build_incident(asset, 'restricted_zone_presence', cluster, [e for e in cluster if e.kind == 'zone_presence']))
            if 'shutdown' in kinds and indicators:
                incidents.append(build_incident(asset, 'abnormal_shutdown', cluster,
                    indicators + [e for e in cluster if e.kind == 'shutdown']))
    return {'schema_version': '1.0', 'engine': 'deterministic_fusion_baseline_v1',
            'incidents': incidents, 'event_graph': {'nodes': graph_nodes, 'edges': graph_edges},
            'input_events': len(by_id), 'accepted_events': len(graph_nodes),
            'limitations': ['Temporal cooccurrence does not establish causality.',
                'Confidence belongs to source events; no calibrated incident probability.',
                'Synthetic fixtures do not measure real-world model performance.']}


def build_incident(asset, kind, cluster, triggers):
    explanations = {
        'acoustic_anomaly': ('Acoustic deviation from learned normal recordings', ['Operating-state change', 'Background noise', 'Mechanical or microphone issue'], 'Listen to the evidence clip and inspect recording conditions.'),
        'operational_warning': ('Controller-reported operational warning', ['Operating load', 'Sensor/controller issue', 'Mechanical condition'], 'Review the original controller code and machine state.'),
        'sensor_anomaly': ('Persistent sensor deviation from learned normal baseline', ['Mechanical problem', 'Changed operating load', 'Sensor measurement issue'], 'Review raw measurements and operating conditions before diagnosing a fault.'),
        'machine_degradation': ('Possible mechanical degradation', ['Bearing wear', 'Loose mounting', 'Sensor or audio measurement error'], 'Review vibration trend and inspect the machine with a qualified technician.'),
        'restricted_zone': ('Possible restricted-zone safety violation', ['Unauthorized entry', 'Authorized maintenance entry', 'Vision detection error'], 'Ask the safety operator to verify the camera evidence and access authorization.'),
        'restricted_zone_presence': ('Person observed inside restricted zone; entry time unknown', ['Authorized presence', 'Unauthorized presence', 'Detection or zone calibration error'], 'Verify camera evidence and access authorization.'),
        'abnormal_shutdown': ('Shutdown following operational warnings', ['Protective shutdown', 'Manual stop', 'Power interruption'], 'Review controller fault codes and operator records before restarting.')}
    title, alternatives, action = explanations[kind]
    return {'incident_id': f'{asset}:{kind}:{cluster[0].event_id}', 'asset_id': asset,
        'type': kind, 'risk': 'MEDIUM' if kind in {'sensor_anomaly','acoustic_anomaly','operational_warning'} else ('HIGH' if kind != 'machine_degradation' or len({e.modality for e in triggers}) >= 3 else 'MEDIUM'),
        'risk_basis': 'Configured rule priority; not a validated safety rating.',
        'start': cluster[0].time.isoformat(), 'end': cluster[-1].time.isoformat(),
        'detected_at': max(e.time for e in triggers).isoformat(),
        'observed_facts': [{'event_id': e.event_id, 'summary': e.summary, 'evidence_ref': e.evidence_ref} for e in cluster],
        'trigger_event_ids': [e.event_id for e in triggers],
        'hypothesis': {'statement': title, 'status': 'unconfirmed', 'alternatives': alternatives},
        'recommended_action': action, 'automatic_action_executed': False,
        'synthetic': any(e.synthetic for e in cluster), 'timeline': [e.to_dict() for e in cluster]}
