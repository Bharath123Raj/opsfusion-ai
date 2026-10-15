# Step 3 verification

Validated with Python 3.12, NumPy 1.26.4, scikit-learn 1.5.2, joblib 1.4.2 and OpenCV 4.10.0.84. CPU execution; no LLM or GPU required.

## Checks

59 software tests pass, including the optional decoded-video integration test. Sensor checks cover temporal leakage prevention, unknown assets, malformed CSVs, nonfinite values, duplicate times, independent evaluation labels, prefix invariance (future data cannot change earlier alert flags), constant-feature scale floors, gap resets, single-sample suppression and protected existing outputs.

Seed-42 synthetic demo was trained on 1600 normal measurements, calibrated on a strictly later 400 normal measurements and evaluated on 800 held-out measurements from two assets. Neither injected fault labels nor test values influence model fitting or calibration.

| Result | Value |
|---|---:|
| Confirmed sensor episodes | 5 |
| Aggregated sensor incident reports | 4 |
| Candidate measurement flags | 160 |
| Persisted alert measurement flags | 144 |
| Point TP / FP / TN / FN | 144 / 0 / 637 / 19 |
| Point precision | 1.0000 |
| Point recall | 0.8834 |
| Point F1 | 0.9381 |
| Matched injected fault intervals | 5 of 6 |
| Interval event recall | 0.8333 |

One-to-one greedy maximum-overlap interval matching is permissive: any positive temporal overlap qualifies. The missed interval is the single-sample spike, intentionally suppressed by three-sample persistence. Abrupt injected shifts were confirmed two seconds after onset; gradual temperature drift was confirmed ten seconds after onset. These delays are measured against synthetic timestamps in batch replay, not live wall-clock latency.

No false persisted alerts occurred in this demo's approximately 0.177 normal asset-hours. That is a short synthetic exposure and does not establish a real-world false-alarm rate or reliability guarantee. The simulator uses simple distributions and conspicuous shifts; independent datasets and realistic operating-state changes are required before accuracy claims.

## Video + sensor composition

A separate integration check merged the 16 Step2 public-video observations with five synthetic sensor observations. It produced six aggregate incident reports, retained their evidence references and kept all hypotheses unconfirmed. Restricted-zone video was not treated as corroboration of mechanical failure. This verifies fusion behavior, not a real joint incident. Only derived synthetic demo summaries are bundled; no user-specific video paths are included.

## Output review

The demo emits raw scores, point evaluation, interval matching results, frozen model thresholds, per-episode measurement evidence and provenance flags. Model bundles are excluded from the ZIP so each user trains a local compatible version. The raw generated data and separate labels are included for reproduction.
