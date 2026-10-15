# Raw sensor CSV contract

UTF-8 CSV with exactly these columns:

```csv
timestamp,asset_id,vibration_rms_mm_s,temperature_c,synthetic
2026-01-01T10:00:00+00:00,machine-a,2.15,55.6,false
```

| Column | Requirement |
|---|---|
| timestamp | ISO 8601 with timezone; UTC normalization is automatic |
| asset_id | Stable nonempty machine/entity identifier, shared with other adapters |
| vibration_rms_mm_s | Finite nonnegative RMS vibration velocity in mm/s |
| temperature_c | Finite machine temperature in degrees Celsius |
| synthetic | true or false, explicitly supplied per record |

Do not use acceleration g, RPM or pressure in the vibration column. Convert units upstream and document the conversion. The current model uses only the two declared features and has no machine speed/load context. Rows can be physically unordered; processing sorts each asset by UTC timestamp while retaining source CSV line numbers. Duplicate timestamps per asset, blanks, NaN and infinity fail explicitly. Labels and fault annotations belong outside inference CSVs; extra columns are rejected.

## Time boundaries

At least 120 training rows and 80 validation rows per asset are required. Both splits must contain exactly the same asset set. Every training time must precede that asset's validation times. Every inference timestamp must follow that asset's validation end. The adapter rejects unseen assets. Known-normal periods are an operator assumption, not something the model verifies.

The model's cadence comes from training timestamps. Persistence is counted per asset, so records from another machine cannot confirm an alert. Three consecutive candidates are the default; a timestamp gap over 2× training median cadence resets confirmation. At one-second cadence, the earliest persisted alert is two seconds after the first candidate, plus processing delay. This is an algorithmic delay, not measured online latency.

## Model files

Train the bundle locally in the same scikit-learn version used for inference. The CLI rejects incompatible recorded versions. `joblib` loads Python serialized objects: use only models you trained or otherwise trust. Demo weights are not shipped; train with the included raw CSVs.

## Evaluation contract

`labels.json` declares `schema: opsfusion_sensor_labels_v1`, `synthetic`, `scope`, and `point_labels`. Each label has `asset_id`, timezone-aware `timestamp` and boolean `anomaly`. Label keys must match all inference records exactly. Optional `fault_intervals` list same-asset start-inclusive/end-exclusive times and fault_type. Labels are evaluated only after detection.

Point precision/recall/F1 measure persisted alert samples. Interval metrics match contiguous alert episodes one-to-one to same-asset fault intervals by greedy maximum overlap; any positive overlap qualifies. This permissive rule is documented rather than presented as causal diagnosis accuracy. False alerts per normal asset-hour use estimated exposure from the median sampling cadence, so irregular sampling needs a more rigorous exposure calculation in a future evaluation.

## Connecting video evidence

Assign the camera to the monitored machine's canonical asset ID, and use the actual recording start time. UTC offsets normalize timezones but do not fix clock drift. If recording times are unknown, label any assigned common anchor as a composed replay. Video safety signals may share graph edges with sensor anomalies, but a person entering a zone is not evidence that a machine has bearing damage.
