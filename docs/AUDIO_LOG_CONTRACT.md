# Audio and log contracts

## Audio manifest

Paths are relative to the JSON manifest's folder unless absolute. Each clip has exactly these four fields. Labels are stored separately.

```json
{"schema":"opsfusion_audio_manifest_v1","clips":[{"path":"normal-01.wav","asset_id":"machine-a","start_time":"2026-01-01T10:00:00+00:00","synthetic":false}]}
```

Format: 16-kHz mono signed PCM16 or floating-point WAV in [-1,1], at least one second, finite samples only. The processor uses complete nonoverlapping one-second windows and discards an incomplete tail, recording its sample count. A recording's full duration, including the tail, still determines overlap checks. Inference time is the full-window availability timestamp. Distinct clips reset confirmation state.

Each asset requires at least 24 train and 12 calibration windows. The tiny included demo is a software/model demonstration and is inadequate for real deployment. Normal training ends before calibration starts. Inference begins after calibration ends. Identical content hashes across training/calibration/test are rejected; same-asset clip intervals may not overlap within a manifest. Splits must cover the same asset set for training and calibration. Unknown inference assets fail. Synthetic training provenance propagates to observations even if an inference clip was marked real, so generated models are not passed off as real baselines.

Training uses a dense autoencoder over log-mel statistics, not a full-spectrogram CNN or pretrained sound classifier. Calibration is also used for early stopping; test audio is never used to choose weights or thresholds. Operator verification of normal calibration and realistic recording diversity remains necessary.

## Log CSV

Exactly these columns:

```csv
timestamp,asset_id,source_id,level,code,message,synthetic
2026-01-01T10:00:07Z,machine-a,controller-01,WARN,TEMP_WARNING,Controller temperature warning,false
```

Timestamps require offsets. IDs and message must be nonempty. synthetic is true/false. Levels: DEBUG, INFO, WARN, ERROR, CRITICAL. Codes are normalized to uppercase. Known warning codes only produce an observation at warning/error/critical severity. Unknown codes and routine heartbeat records are retained as unmapped metadata, not guessed from free text. Structured log adapters for vendor-specific formats should define an explicit code map rather than modifying message text to fit demo codes.

No planned stop is treated as abnormal merely because the message says shutdown. Recognized protective/unexpected codes produce controller-reported shutdown observations. Observed logs do not prove a physical fault. Exact normalized record duplicates are removed; event references preserve source CSV line numbers.

## Fusion

`opsfusion.combine` accepts one or more adapter JSONL files, validates event records, rejects conflicting IDs and deduplicates identical observations. Standalone audio/log report categories are enabled in Step4 commands; legacy calls retain previous behavior. Same-asset bounded 90-second windows establish graph cooccurrence, not causality. If clocks or asset mappings differ, related events may remain separate. Public videos and generated signals sharing an assigned start time are composed replay, not real synchronized evidence.
