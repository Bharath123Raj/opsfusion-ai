# Event contract v1

One JSON object per line; all fields required. Unknown fields fail validation.

| Field | Meaning |
|---|---|
| event_id | Unique observation ID; reuse only for an identical duplicate |
| timestamp | ISO 8601 time including UTC offset; normalized to UTC |
| asset_id | Canonical machine/zone ID established by the adapter |
| modality | sensor, video, audio, log, maintenance, note |
| kind | vibration_high, temperature_high, abnormal_noise, motor_warning, zone_entry, zone_presence, shutdown, recent_repair, operator_note |
| source_id | Sensor, camera, log stream or ticket system identifier |
| evidence_ref | Reference to the underlying record, frame or audio segment |
| summary | Adapter's observed fact, without unsupported cause claims |
| confidence | Finite source confidence in [0,1]; not an incident probability |
| synthetic | Boolean marking simulated evidence |

```json
{"event_id":"sensor-001","timestamp":"2026-01-01T15:30:00+05:30","asset_id":"machine-a","modality":"sensor","kind":"vibration_high","source_id":"vib-22","evidence_ref":"synthetic://vib-22/001","summary":"Synthetic elevated vibration","confidence":0.9,"synthetic":true}
```

Production adapters should use a stable evidence URI with offset/range information and preserve raw data separately. In this demo `synthetic://` references identify fixture observations and do not point to actual media. Clock offsets must be corrected upstream; the engine normalizes timezones but does not estimate clock drift. Current clusters begin at their earliest accepted event and cover at most 90 seconds. Batch processing includes future context within a cluster, so `detected_at` describes available triggers, not measured live latency.

The graph contains accepted observation nodes and pairwise same-asset cooccurrence edges within each cluster. Sensor-only duplicates cannot provide cross-modality corroboration. Different modalities are a heuristic for corroboration, not proof of statistical independence.

Step 2 extends the contract with zone_presence: person first observed inside, with no verified outside-to-inside crossing. The confidence field holds the confirming detector score. Video event time is the confirmation sample, not a claimed exact boundary-crossing time. Evidence references identify a local snapshot and source-video offset.

Step 3 adds sensor_anomaly for a computed persistent deviation. Its confidence=1.0 is an adapter acceptance marker, not a calibrated fault probability. Detector scores and raw measurements live in the evidence JSON. The observation time is the causal confirmation sample; a completed episode evidence file also contains subsequent context.

Step4 reuses abnormal_noise and motor_warning/shutdown event kinds. Step4 combine/audio/log commands enable standalone acoustic_anomaly and operational_warning incident categories. These categories are incident types, not new event kinds. Evidence scores and original source offsets remain separate from unconfirmed causes.
