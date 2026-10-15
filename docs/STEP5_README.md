# OpsFusion AI — Step 5 (v0.5.0)

Virtual incident replay, immutable first-alert snapshots, independent incident labels, one-to-one matching, modality ablations and a timestamp-offset stress check.

This cumulative package includes Steps 1–4. Extract Step5 separately and open PowerShell inside the folder containing this README. Reuse your existing `opsfusion312` environment and keep previous folders containing model weights and four-source outputs. No new dependency is required for the Step5 commands, which use Python's standard library.

## 1. Run software checks

```powershell
conda run -n opsfusion312 python -m unittest discover -s tests -v
```

Expected: 106 tests pass with your prior audio/sensor/video dependencies. The new checks cover first-alert immutability, no future observation IDs, equal-timestamp batching, deterministic run IDs, deduplication, matching, label coverage, normal exposure, HTML escaping and the known fixed-window limitation.

## 2. Replay your four-source observations

Your Step4 combine command wrote a complete `combined_events.jsonl`. Replay that file:

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.replay --events "$env:USERPROFILE\Downloads\OpsFusion-AI-Step4\OpsFusion-AI-Step4\runs\four-source-demo-01\combined_events.jsonl" --output .\runs\four-source-replay-01
```

Expected for your previous run: 26 observations, 10 first alerts across all categories. Review the readable report:

```powershell
Start-Process .\runs\four-source-replay-01\report.html
notepad .\runs\four-source-replay-01\run_manifest.json
```

The report has an emission timeline and expandable evidence sections. Causes remain unconfirmed, and recommendations do not execute actions. It is a local review report, not the full dashboard or a hosted service. Original evidence references point to your previous output folders, which must remain available.

Replay also accepts several JSONL inputs after `--events`; do not include both a combined file and separate copies unless identical-event deduplication is intended. Conflicting duplicate IDs fail explicitly. Output folders are protected; choose `four-source-replay-02` for a repeat run.

## 3. Run the independent synthetic incident benchmark

The authored corpus and separate labels are included in `data/incident-demo`. It contains 24 adapter observations, eight incident labels and ten scenarios:

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.benchmark run --events .\data\incident-demo\events.jsonl --labels .\data\incident-demo\labels.json --output .\runs\incident-benchmark-01
```

Expected: nine variants; all-source TP=7, FP=0, FN=1. Open the comparison:

```powershell
Start-Process .\runs\incident-benchmark-01\ablation.csv
notepad .\runs\incident-benchmark-01\all.json
```

| Variant | Purpose |
|---|---|
| all | All adapter modalities |
| sensor_only, video_only, audio_only, log_only | Single-source baselines |
| without_audio, without_video, without_log | Missing-source ablations |
| audio_clock_plus30s | Audio event timestamps shifted 30 seconds; labels retain the original times |

The timestamp variant simulates clock offset, not transport delay or delayed packet arrival. Results evaluate the existing fusion rules downstream of adapter observations; the benchmark does not run video/audio/sensor models or prove perception accuracy. Ground truth is manually authored from scenario descriptions, not generated from fusion outputs. Its scope is machine_degradation, restricted_zone and abnormal_shutdown. Standalone sensor, acoustic, operational-warning and presence reports remain reviewable but are outside this benchmark's evaluated categories.

## 4. Inspect one benchmark replay and evaluate it

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.replay --events .\data\incident-demo\events.jsonl --output .\runs\labelled-replay-01

conda run -n opsfusion312 python -m opsfusion.incident_eval --replay .\runs\labelled-replay-01\replay.json --labels .\data\incident-demo\labels.json --output .\runs\labelled-replay-01\metrics.json
```

This authored replay emits 23 first alerts across all categories. Evaluation considers only the three declared categories, producing seven matched target alerts against eight labels. Do not confuse the full report count with the evaluation target count.

Do not apply these labels to your four-source demo: different assets and coverage make them invalid for that run. Independent labels must be written for the actual run being scored. There are no verified physical-fault labels for the composed public-video/generated-signal demo.

## Meaning of the metrics

The all-source synthetic result is precision=1.0, recall=0.875, F1≈0.9333, matched median detection delay=5 seconds in virtual event time. No matched alert claims a confirmed mechanical cause. These metrics assess detection of authored candidate incident categories, not diagnosis of real faults.

Matching is maximum-cardinality one-to-one with identical asset and category. A first-emission time must lie in the label's start/end interval. Optional `--tolerance-seconds` allows a specified late margin, recorded in the result; early matches are never accepted. Extra predictions cannot share a label. Missing precision or delay is null when undefined, not a misleading perfect score.

False-alert rate uses declared normal asset-hours. False positives inside a fault-covered interval remain FP, but are not counted in the normal-exposure rate. The small authored corpus has only about 0.21 normal asset-hours, so a zero rate is not evidence of deployment reliability.

## Known limitation intentionally exposed

One scenario contains two distinct faults 40 seconds apart on the same asset. The fixed 90-second correlation window groups their signals into one incident and emits only one first alert. The second independent label is missed. We retain this failure so the benchmark exposes a real segmentation limit rather than claiming perfect performance. A future episode-aware incident segmenter should be evaluated against the same unchanged labels.

## Replay semantics and causal limits

The replay sorts adapter availability timestamps and admits equal-time records as one batch. At each timestamp it re-runs the reference fusion on the available prefix. It stores first-alert snapshots separately from later updates. New observation IDs cannot be added retroactively to the first snapshot. Stable run IDs are hashes of canonical observations, replay version and window configuration; changed input/configuration changes the ID.

This is virtual-time replay, not live stream ingestion. It does not simulate transport delays, late arrivals, watermarks or wall-clock notification latency. The reference implementation retains history and rebuilds prefix graphs, so its CPU/memory behavior is not a production streaming benchmark.

Snapshots freeze observation records, not external evidence-file bytes. Earlier batch adapters may reference completed episode JSON/WAVs and summaries that include later context. Step5 does not verify or truncate those artifacts to first-emission time. Future-observation-ID tests therefore establish event-level prefix behavior, not complete causal media evidence. Keep that distinction when presenting the demo.

## Outputs

| File | Purpose |
|---|---|
| replay.json | Stable run ID, first alerts, updates and final fusion report |
| report.html | Readable first-alert timeline and evidence review |
| alerts.jsonl | Frozen first-alert snapshots |
| updates.jsonl | Later evidence/risk updates |
| incidents.json | Final batch-context incident report |
| run_manifest.json | Counts and replay assumptions |
| ablation.csv | Variant comparison |
| all.json and other variant JSONs | Matches, false positives, missed labels, delays and scope |
| summary.json | Benchmark results and input hashes |

Previous instructions are preserved in `docs/STEP4_README.md`, `docs/STEP3_README.md` and `docs/STEP2_README.md`. Generated audio and sensor data remain included. Model weights and runtime-specific run folders are not bundled.

Next: improve incident segmentation using this benchmark, add independent real-data labels, then evidence-grounded reasoning and the dashboard. Do not claim real factory accuracy from authored adapter scenarios.
