Step15 update: the optional authenticated gateway now gates Streamlit/media
traffic when the backend remains private. Persistent data, supervision and
backup/restore are documented in [DEPLOYMENT.md](DEPLOYMENT.md). Direct local
8504 access retains its earlier limitations; all accounts still share one site.

Step13 update: opt-in active camera/HTTP source links now produce bounded joint
cooccurrence reports. See [LINKED_SOURCES.md](LINKED_SOURCES.md) for current
linking, clocks, gap boundaries and first/current evidence rules. Earlier
session-scoped fusion below remains available and keeps its existing reports.

This document covers uploaded recordings/CSV/WAV files. Continuous HTTP
sensor/controller ingestion is documented in [HTTP_STREAMS.md](HTTP_STREAMS.md).

# Real recording integration — Step 8 v0.8.1

The earlier v0.8.0 screen reviewed event files. This revision connects raw uploads to the actual perception adapters. No prewritten incident-demo observations are imported into a raw run, and missing baselines do not trigger demo-data substitution.

## Start

Open PowerShell in the new folder containing dashboard.py. Reuse opsfusion312, install all requirements listed in the README, and launch Streamlit. Stop an older dashboard with Ctrl+C before restarting the new folder.

Leave **Observation source → Real recordings** selected. Enable only the sources you have. Missing selected files produce an explicit error. Analyse any one source independently, or upload multiple aligned recordings for a combined run.

## Video

1. Upload a camera recording (MP4, AVI, MOV or MKV; at most 100 MiB).
2. Enter the actual recording start including timezone, for example a known local time using `+05:30`. Do not use processing time as recording time.
3. Enter its camera ID and canonical asset ID.
4. Set the polygon as 3–32 `[x, y]` vertices, normalized from 0 to 1 across the image. The initial coordinates are an example; they are not the correct zone for every camera.
5. Click **Preview zone on first frame** and check the drawn boundary. Change the polygon if necessary.
6. Choose up to 60 seconds and 1–10 sample FPS; default 20 seconds at 5 FPS. Click **Analyse recordings and fuse incidents**.

The normal path loads official YOLOv8n weights and predicts people on decoded frames. First use may download those weights. It distinguishes confirmed outside-to-inside transitions from initial presence with unknown entry time. An entry does not establish authorization. An annotated MP4 and event snapshots are generated; use the direct download to view the MP4 in a compatible desktop player. The annotated preview has no audio.

Input should be constant-frame-rate video. Variable-frame-rate material needs explicit conversion. This is recorded-file processing; no live RTSP or webcam connection is implemented in this revision.

## Sensors

Upload the raw capture CSV with exactly:

```csv
timestamp,asset_id,vibration_rms_mm_s,temperature_c,synthetic
```

Use measured vibration velocity RMS in mm/s and machine temperature in Celsius. Set synthetic to false for genuinely measured records; keep generated records true. CSV limits: 5 MiB and 20,000 data rows per file. The two modeled features and required units are fixed; unrelated sensor schemas need an explicit upstream adapter.

Choose either:

- **Train from normal files:** upload known-normal training and calibration CSVs. Each asset needs at least 120 training rows and 80 calibration rows. Check that you reviewed those periods as normal. Training must precede calibration; every capture time must follow calibration. Assets must match across baseline splits; unseen capture assets fail.
- **Use a trusted local model:** enter the full path to your earlier sensor.joblib and confirm its trust. It must be from the correct machine/assets and compatible library version. Models uploaded as arbitrary serialized files are not accepted.

The adapter computes Isolation Forest scores, robust residuals, persisted candidates and anomaly episodes, preserving original CSV line references. The source ZIP includes scored.csv and per-episode evidence. A baseline trained on generated data propagates synthetic provenance even when the capture CSV itself says false.

## Audio

Upload a machine WAV: 16-kHz mono signed PCM16 or floating-point audio in [-1,1], at least one second, at most 20 MiB. Enter its actual start time and the same physical asset ID. Other rates or stereo are rejected; there is no silent conversion.

Choose either:

- **Train from normal files:** upload separate known-normal training and calibration WAVs, enter their actual recording starts, and confirm normal-period review. Need at least 24 train seconds and 12 calibration seconds. Their intervals must precede the analysed clip. Identical training/calibration/test content and invalid overlaps are rejected. Declare generated baseline recordings explicitly.
- **Use a trusted local model:** enter the path to your correct per-asset audio.pt and confirm trust. A baseline from a different asset or an inference clip before calibration fails.

Log-mel features, autoencoder scores and persistence produce acoustic episodes. Their cited JSON evidence and WAV snippets are directly linked in the incident viewer. Deviation from a learned normal sound is not a fault diagnosis. The minimum dataset sizes are software requirements, not adequate evidence of production readiness.

## Controller records

Upload CSV with exactly:

```csv
timestamp,asset_id,source_id,level,code,message,synthetic
```

Known warning codes: TEMP_WARNING, VIBRATION_WARNING, MOTOR_OVERLOAD at WARN/ERROR/CRITICAL levels. Known shutdown codes: UNEXPECTED_SHUTDOWN, PROTECTIVE_SHUTDOWN. Unknown codes and normal heartbeat records are retained as unmapped metadata. There is no free-text guessing of physical causes or automatic support for every PLC vendor's log format. Limits: 5 MiB / 20,000 data rows.

## Combined runs and evidence

Sources share incidents only according to the existing same-asset temporal rules. They must genuinely describe the same physical asset and use correct clocks. Timezone normalization does not correct drift, missing timestamps or unrelated source recordings. A public video plus generated sensor/audio/log files with assigned matching starts is a composed demonstration.

Raw run outputs are durable local files under a unique runs/raw-ingestion folder. No prior run is overwritten. A successfully completed raw run is loaded into the same timeline, first/final evidence, cited explanation and optional independently labelled benchmark views.

The review ZIP holds observations/replay/explanations. The separate source ZIP holds generated evidence, scores, manifests, CSV inputs and newly trained model artifacts. Original uploaded video/audio is excluded unless requested. References and hashes are recorded; moving/exporting source folders does not automatically rewrite original absolute references. Use the source ZIP's structured paths to find evidence elsewhere.

Interactive review supports at most 200 canonical adapter observations. Complete adapter files are retained if that limit is exceeded, but the run is marked failed for the UI; use the existing CLIs for larger captures. There is no silent truncation to make a run pass.

## What has been verified

Actual YOLOv8n CPU inference on public OpenCV vtest.avi: 20 seconds, 100 sampled frames, 16 observations (6 entries / 10 initial-presence), 2 first alerts, 16 generated evidence records. This verifies decoded-media integration; no person/entry ground-truth labels were supplied, so it does not establish accuracy.

Four-source orchestration tests train real model code and execute raw inference using explicitly synthetic sensor/audio/log fixtures plus scripted test detections. Their provenance stays synthetic. Browser checks separately use actual YOLO inference on uploaded public video and a synthetic controller fixture. No user's real industrial recordings or independent industrial fault labels have been evaluated here.
