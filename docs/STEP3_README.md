# OpsFusion AI — Step 3 (v0.3.0)

Raw sensor measurements → per-machine normal baseline → anomaly scoring → causal persistence → timestamped evidence → OpsFusion incident reports.

This cumulative package includes Steps 1 and 2. Keep the old Step2 folder containing your videos and runs. Extract Step3 separately and run new commands from the folder containing this README. Reuse your working `opsfusion312` Conda environment; no Python or Torch reinstall is needed.

## Windows PowerShell quick start

Open PowerShell inside the extracted `OpsFusion-AI-Step3` folder:

```powershell
conda run -n opsfusion312 python -m pip install -r requirements-sensor.txt
conda run -n opsfusion312 python -m unittest discover -s tests -v
```

Expected: 59 passing tests with your existing OpenCV dependencies. If you only install sensor dependencies on a new machine, the optional video integration test is skipped. Tests include time separation, asset isolation, prefix invariance, invalid data, consecutive confirmation, constant sensor scale floors, protected outputs and separate-label evaluation.

Synthetic demo CSVs are already included; train your model locally:

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.sensor train --train .\data\sensor-demo\train.csv --validation .\data\sensor-demo\validation.csv --output .\models\sensor-v1
```

Then process the held-out test period:

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.sensor infer --csv .\data\sensor-demo\test.csv --model .\models\sensor-v1\sensor.joblib --labels .\data\sensor-demo\labels.json --output .\runs\sensor-01
```

Expected seed-42 demo result: `800 measurements, 5 sensor episodes, 4 fused incidents`. Two episodes on the first asset fall within the same bounded 90-second fusion window, explaining why episode and incident counts differ. Inspect:

```powershell
notepad .\runs\sensor-01\run_manifest.json
notepad .\runs\sensor-01\metrics.json
notepad .\runs\sensor-01\incidents.json
Start-Process .\runs\sensor-01\scored.csv
```

`scored.csv` can be opened in Excel or a text editor. Raw values, robust deviations, IF scores, candidate flags and causal alert flags are available for every measurement. Existing model/run folders are protected; choose `sensor-v2` or `sensor-02` for another run. Generation uses a separate command if you want another seed:

```powershell
conda run -n opsfusion312 python -m opsfusion.sensor demo --seed 43 --output .\data\sensor-demo-seed43
```

## What the model learns

Each asset gets its own Isolation Forest trained only on its training period. Median/MAD scaling also uses training data only. An anomaly threshold is frozen from the later, operator-supplied normal validation period (99.5th percentile of negated IF scores).

A candidate reading has either an absolute robust deviation of at least 6, or an IF score above the validation threshold with a robust deviation of at least 3. Three consecutive candidates confirm an alert. The confirmation time is the third sample, never a backdated first sample. A gap exceeding twice the asset's training median cadence resets the streak. Single-sample spikes are intentionally suppressed.

The model has no access to test labels during training, calibration or detection. Labels are read only when `--labels` requests evaluation after detection. This is classical ML inference over raw sensor values; it is not a sensor neural network.

## Included data and measurements

| File | Rows | Purpose |
|---|---:|---|
| data/sensor-demo/train.csv | 1600 | Normal synthetic training measurements |
| data/sensor-demo/validation.csv | 400 | Later normal synthetic threshold calibration |
| data/sensor-demo/test.csv | 800 | Held-out synthetic measurements with injected faults |
| data/sensor-demo/labels.json | 800 point labels, 6 intervals | Evaluation only |
| data/sensor-demo/source.json | — | Seed, generation history, time anchor and units |

Two assets are simulated with vibration RMS in mm/s and machine temperature in degrees Celsius. Faults include vibration bias, temperature bias, a multivariate shift, gradual temperature drift and a single-sample spike. The replay begins at `2026-01-01T10:00:00Z` so it can be composed with the Step2 test video, but there is no real-world relationship between those data sources.

## Outputs

| File | Meaning |
|---|---|
| models/sensor-v1/sensor.joblib | Locally trained per-asset baselines |
| models/sensor-v1/training_manifest.json | Training windows, scalers, frozen thresholds, hashes and versions |
| runs/sensor-01/scored.csv | Per-reading scores, candidates and causal persisted flags |
| runs/sensor-01/evidence/*.json | Episode measurements with original CSV line references |
| runs/sensor-01/sensor_events.jsonl | One sensor_anomaly observation per confirmed episode |
| runs/sensor-01/combined_events.jsonl | Sensor observations and optional merged source observations |
| runs/sensor-01/incidents.json | Sensor-only and optionally multimodal incident candidates |
| runs/sensor-01/metrics.json | Optional point and one-to-one interval evaluation |
| runs/sensor-01/run_manifest.json | Completion status, settings, counts and provenance |

Step3 adds `sensor_anomaly` to the shared event contract and a medium-priority sensor incident candidate. An accompanying motor warning or abnormal audio observation may also produce a machine-degradation hypothesis. A zone-entry video alone does not corroborate a mechanical cause.

The event confidence field uses `1.0` as an adapter acceptance marker for a validated computed observation. It is NOT a probability that the machine is faulty. IF scores and robust deviations carry the detector strength separately. Explanations remain unconfirmed, and no shutdown or maintenance action is executed.

## Optional: compose with your Step2 video evidence

After the sensor-only run, you can demonstrate event-graph correlation using your existing video events. The video replay anchor must match, and the default asset IDs match the example config:

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.sensor infer --csv .\data\sensor-demo\test.csv --model .\models\sensor-v1\sensor.joblib --labels .\data\sensor-demo\labels.json --merge-events "..\..\OpsFusion-AI-Step2\OpsFusion-AI-Step2\runs\opencv-smoke-01\video_events.jsonl" --output .\runs\sensor-video-demo-01
```

That relative path assumes your existing double-folder Downloads layout. If different, use the actual absolute path to your Step2 `video_events.jsonl`. This is an optional composed demo: real public video plus synthetic sensors, not a real synchronized incident. Synthetic sensor provenance propagates to aggregate reports containing that evidence. Do not edit the video source events to conceal provenance.

## Real sensor data

Follow `docs/SENSOR_CONTRACT.md`. Supply known-normal train and validation CSVs plus a strictly later inference CSV, with exactly the declared columns. Use the same asset names throughout. Set `synthetic=false` only for real records. Train a new model on your actual machine's baseline; demo-trained models are not appropriate for real sensor deployment. Omit `--labels` when no independent ground truth is available. No API key is needed.

## Honest scope and next step

Seeded synthetic evaluation shows pipeline behavior, not real factory fault accuracy. Temperature and vibration may change naturally with operating load. This raw-feature baseline lacks load/operating-state context and does not detect every drift, frozen sensor, missing-data condition or acoustic fault. Missing measurements fail explicitly instead of being silently imputed. Batch inference and episode summaries are not a live sensor service; later rows appear in the completed evidence file, while the recorded confirmation timestamp is causal.

Next: actual audio anomaly inference and structured log adapters, followed by synchronized incident benchmarks and the dashboard. See `docs/STEP3_VALIDATION.md` for verified results.

Isolation Forest documentation: https://scikit-learn.org/1.5/modules/generated/sklearn.ensemble.IsolationForest.html
Video instructions from Step2: `docs/STEP2_README.md`.
