# OpsFusion AI — Step 6 (v0.6.0)

Configurable episode segmentation, preserving the fixed-window baseline and comparing both against unchanged incident labels.

This cumulative package includes Steps 1–5. Extract Step6 separately, open PowerShell in the folder containing this README, and reuse your existing `opsfusion312` Conda environment. No new dependency or model training is required for Step6. Keep previous folders containing your four-source adapter output and media evidence.

## 1. Check the software

```powershell
conda run -n opsfusion312 python -m unittest discover -s tests -v
```

Expected: 128 tests pass with your prior dependencies. Twenty-two new checks cover gap boundaries, short/long episode behavior, source families, context bridging, stale corroboration, stable IDs, first-alert snapshots and the deliberately retained failure cases.

## 2. Compare both engines on the unchanged Step5 corpus

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.segment_compare --events .\data\incident-demo\events.jsonl --labels .\data\incident-demo\labels.json --output .\runs\segmentation-compare-01
```

Expected:

```text
fixed: TP=7, FP=0, FN=1
 episode: TP=8, FP=0, FN=0
```

The Step5 events and labels in this ZIP are byte-for-byte unchanged. The new engine detects the second authored fault that the original 90-second window merged into the first. Open the comparison and detailed evidence:

```powershell
Start-Process .\runs\segmentation-compare-01\comparison.csv
Start-Process .\runs\segmentation-compare-01\episode\report.html
notepad .\runs\segmentation-compare-01\summary.json
```

An 8/8 result on this small development corpus is not a factory accuracy result. The failure was already known while implementing Step6, so this improvement is a development regression result, not blind validation.

## 3. Run the separate segmentation regression corpus

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.segment_compare --events .\data\segmentation-regression\events.jsonl --labels .\data\segmentation-regression\labels.json --output .\runs\segmentation-regression-01 --gap-sweep 5 10 20 30 45
```

Expected at the default 20-second gap:

| Engine | TP | FP | FN | F1 |
|---|---:|---:|---:|---:|
| Original fixed window | 6 | 4 | 1 | 0.7059 |
| New episode segmentation | 6 | 1 | 1 | 0.8571 |

This separate authored corpus covers eight scenarios and seven labelled incidents. It tests long continuous warnings, short gaps, separate faults, notes and safety activity that must not bridge mechanical evidence, old warnings unrelated to a stop, sparse continuing faults and slow corroboration. It was constructed to probe the heuristic and is not a blind holdout or real machine dataset.

The new engine still has two important failures: a continuing fault with sparse observations can produce an extra alert, and a second source arriving after the gap threshold can prevent corroboration. These failures remain visible rather than being removed from labels. The optional sweep reports sensitivity; it does not choose a winning threshold or establish an independent tuned test score.

```powershell
Start-Process .\runs\segmentation-regression-01\comparison.csv
notepad .\runs\segmentation-regression-01\episode\metrics.json
notepad .\runs\segmentation-regression-01\summary.json
```

## 4. Replay your four-source demonstration with episodes

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.replay --events "$env:USERPROFILE\Downloads\OpsFusion-AI-Step4\OpsFusion-AI-Step4\runs\four-source-demo-01\combined_events.jsonl" --engine episode --quiet-gap-seconds 20 --output .\runs\four-source-episodes-01

Start-Process .\runs\four-source-episodes-01\report.html
```

For the previously supplied 26 observations, the new replay emits 11 first alerts across all categories; the fixed baseline emitted 10. The changed count is segmentation behavior, not independently verified improvement on that composed replay. There are no physical-fault ground-truth labels for it. Inspect the new `episodes` section in `incidents.json` and the configuration in `run_manifest.json`.

The original engine remains available with `--engine fixed`, which is still the default for backwards compatibility. Existing Step5 benchmarks and adapter commands retain their old behavior unless episode replay is explicitly selected. Gap and engine configuration are included in the run identity; numeric equivalents such as 20 and 20.0 generate the same episode run ID.

Existing output folders are protected. Choose a new name for another run.

## How the new heuristic works

1. Mechanical activity and zone-safety activity are tracked separately for each asset.
2. A new episode begins when the gap since the previous accepted qualifying observation is greater than the configured quiet gap (20 seconds by default). An exact 20-second gap stays in the same episode.
3. Low-score observations are filtered before segmentation. Maintenance/operator context can be attached nearby, but cannot extend activity or change the anchor ID. Safety activity cannot keep a mechanical episode alive.
4. Continuous accepted mechanical warnings can keep the same episode alive beyond 90 seconds, avoiding a duplicate alert caused solely by the original fixed duration cap.
5. Mechanical corroboration still requires different-modality indicator pairs no more than 90 seconds apart. Old sources cannot corroborate merely because their records remain somewhere in a long episode. Controller shutdown reports require a prior warning within that horizon in the same episode.
6. First confirmations remain fixed while later evidence updates the episode report. No extra observation IDs are inserted into an earlier alert snapshot, and causal hypotheses remain unconfirmed.

A quiet interval here means absence of adapter observations. It does NOT establish normal readings, recovery or a completed maintenance action. Earlier adapters emit one confirmation event per anomaly episode rather than continuous activity updates; their sparse output can therefore make a physically continuing fault look quiet. The regression corpus demonstrates this ambiguity. Operational deployment needs explicit ongoing/clear state observations or independent reviewed episode labels before treating gap splits as recovery.

The 90-second horizon now bounds corroborating pairs and graph edges in the episode engine, rather than capping every episode's duration. Nearby context is selected relative to the episode anchor; this is not historical maintenance retrieval.

## Outputs and reproducibility

| File | Meaning |
|---|---|
| comparison.csv | Fixed-versus-episode metrics on identical labels |
| summary.json | Source hashes, run IDs, settings, detailed metrics and optional sensitivity sweep |
| fixed/replay.json and episode/replay.json | Immutable first alerts and later updates for each engine |
| fixed/report.html and episode/report.html | Human-readable evidence review |
| fixed/metrics.json and episode/metrics.json | Matches, misses, false predictions and declared scope |
| episode/incidents.json | Final reports and episode boundary evidence |
| reports/step6_original_comparison.json | Verified original-corpus comparison |
| reports/step6_regression_comparison.json | Verified separate-regression comparison |

Reference labels are not used by the segmenter. The comparison tool supplies the same observations to both engines and only then evaluates against labels. It preserves the Step5 evaluator and all of its category/coverage/matching rules. With no independent normal exposure, false-alarm rates remain undefined rather than invented.

## Current limits and next step

This remains virtual replay over adapter events. There is no live transport, out-of-order arrival handling, clock correction, verified physical recovery or real factory accuracy claim. First-alert snapshots freeze records, while referenced batch episode artifacts may still contain later context. The full-history reference implementation is not a bounded production streaming service.

Next: evidence-grounded reasoning and an incident review dashboard, while retaining these failure probes. Real-data episode labels, ongoing/clear signals, operating-state context and live streaming behavior remain outstanding.

Earlier usage guides are in docs/STEP5_README.md, docs/STEP4_README.md, docs/STEP3_README.md and docs/STEP2_README.md.
