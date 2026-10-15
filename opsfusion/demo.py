"""Reproducible synthetic scenarios, with truth stored separately from inputs."""
from datetime import datetime, timedelta, timezone
import random
from .core import Event


def generate(seed=42):
    rng = random.Random(seed)
    events, truth = [], []
    base = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    scenarios = [
        ('machine-a', [('sensor', 'vibration_high', 0), ('log', 'motor_warning', 14), ('audio', 'abnormal_noise', 25), ('maintenance', 'recent_repair', 30)], ['machine_degradation']),
        ('machine-b', [('video', 'zone_entry', 0)], ['restricted_zone']),
        ('machine-c', [('sensor', 'temperature_high', 0), ('log', 'motor_warning', 12), ('log', 'shutdown', 35)], ['machine_degradation', 'abnormal_shutdown']),
        ('machine-d', [('sensor', 'vibration_high', 0), ('sensor', 'vibration_high', 10)], []),
        ('machine-e', [('log', 'operator_note', 0)], []),
        ('machine-f', [('sensor', 'vibration_high', 0), ('audio', 'abnormal_noise', 180)], []),
    ]
    for index, (asset, signals, expected) in enumerate(scenarios):
        for modality, kind, offset in signals:
            event_id = f'evt-{len(events)+1:04d}'
            timestamp = base + timedelta(minutes=index*10, seconds=offset)
            events.append(Event(event_id, timestamp.isoformat(), asset, modality, kind,
                f'{asset}-{modality}', f'synthetic://{asset}/{event_id}',
                f'Synthetic {kind.replace("_", " ")} observation on {asset}',
                round(rng.uniform(.85, .98), 3), True))
        truth.extend({'asset_id': asset, 'type': kind} for kind in expected)
    return events, truth
