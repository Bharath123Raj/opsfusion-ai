# OpsFusion AI — Step 4 (v0.4.0)

WAV audio → log-mel features → trained PyTorch autoencoder → acoustic deviations.
Structured controller logs → explicit code mapping → operational events.
Together → OpsFusion evidence graph, timeline and unconfirmed hypotheses.

This cumulative package includes Steps 1–3. Extract Step4 separately and open PowerShell in the folder containing this README. Keep earlier folders containing your model weights, video and sensor outputs. Reuse your existing `opsfusion312` Conda environment (Python 3.12, Torch 2.5.1); no GPU, API key or paid service is required.

## 1. Install and test

```powershell
conda run -n opsfusion312 python -m pip install -r requirements-audio.txt
conda run -n opsfusion312 python -m unittest discover -s tests -v
```

Expected: 81 passing tests with your previous sensor/OpenCV dependencies installed. On a clean machine, also install requirements-sensor.txt and requirements-vision.txt as appropriate. Tests include actual WAV decoding, feature dimensions, content separation, chronological splits, causal confirmation, weights-only model loading, explicit log mapping and prior sensor/video/fusion behavior.

## 2. Train the audio model

Generated WAVs are already included in `data/audio-demo`. Train on normal training recordings and calibrate on later normal recordings:

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.audio train --train .\data\audio-demo\train.json --validation .\data\audio-demo\validation.json --output .\models\audio-v1
```

This trains a small dense autoencoder on CPU, at most 200 epochs with early stopping. It is a neural network trained locally, not a pretrained audio classifier. Check `models/audio-v1/training_manifest.json` for training losses and thresholds.

## 3. Infer from the held-out WAV

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.audio infer --manifest .\data\audio-demo\test.json --model .\models\audio-v1\audio.pt --output .\runs\audio-01
```

Expected seed-42 result: `30 windows, 2 confirmed episodes`. Two consecutive anomalous one-second windows are required. A window is timestamped when its full samples are available, not at its beginning. Each recording starts fresh confirmation state; fragments are not stitched together.

## 4. Parse the controller logs

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.logs --csv .\data\audio-demo\logs.csv --output .\runs\logs-01
```

Expected: `6 records, 3 mapped events`. TEMP_WARNING, VIBRATION_WARNING and MOTOR_OVERLOAD map to motor_warning only at WARN/ERROR/CRITICAL levels. UNEXPECTED_SHUTDOWN and PROTECTIVE_SHUTDOWN map to reported shutdown observations. Heartbeats and unknown codes remain in the parser's unmapped list; message wording cannot create a fault event. Synthetic logs have explicit provenance.

## 5. Combine audio and logs

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.combine --events .\runs\audio-01\audio_events.jsonl .\runs\logs-01\log_events.jsonl --output .\runs\audio-logs-01
```

Expected: `5 observations, 4 incidents`. The four reports are acoustic anomaly, operational warning, possible machine degradation and abnormal shutdown. These are overlapping report categories for the same replay, not four independently verified faults. Causes remain unconfirmed; no operational action is executed.

```powershell
notepad .\runs\audio-logs-01\incidents.json
notepad .\runs\audio-01\run_manifest.json
Start-Process .\runs\audio-01\scores.csv
```

Listen to the generated normal/test WAVs and exported evidence clips:

```powershell
Start-Process .\data\audio-demo\train-0.wav
Start-Process .\data\audio-demo\test-0.wav
Get-ChildItem .\runs\audio-01\evidence\*.wav
```

Existing output folders are protected. Use `audio-v2`, `audio-02` or another new name to rerun. Paths resolve relative to the current folder. Evidence JSONs include source WAV hashes, original offsets and per-window scores; matching evidence WAVs contain the episode audio.

## 6. Optional separate-label evaluation

```powershell
conda run -n opsfusion312 python -m opsfusion.audio_eval --scores .\runs\audio-01\scores.csv --labels .\data\audio-demo\labels.json --output .\runs\audio-01\metrics.json
```

Seed-42 generated demo: TP=10, FP=0, TN=18, FN=2. Window precision=1.0 and recall≈0.8333. The first abnormal window in each interval is unconfirmed until the second window arrives. These are generated spectral deviations, not bearing-failure recordings or real machine accuracy. Labels are read only by this optional evaluator after inference. The helper evaluates one fully labelled clip and verifies its time anchor and window coverage.

## 7. Optional four-source composed replay

Use the outputs you already generated in earlier steps:

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.combine --events .\runs\audio-01\audio_events.jsonl .\runs\logs-01\log_events.jsonl "$env:USERPROFILE\Downloads\OpsFusion-AI-Step3\OpsFusion-AI-Step3\runs\sensor-01\sensor_events.jsonl" "$env:USERPROFILE\Downloads\OpsFusion-AI-Step2\OpsFusion-AI-Step2\runs\opencv-smoke-01\video_events.jsonl" --output .\runs\four-source-demo-01
```

Adjust earlier folder paths if your extraction layout differs. Their assigned replay anchors match `2026-01-01T10:00:00Z`, and the example asset is warehouse-zone-01. This combines public pedestrian video with generated sensors, generated audio and synthetic controller records. It is a composed demonstration, not a real synchronized factory incident. The original real-video events retain their provenance; reports containing synthetic evidence are marked synthetic.

## Real WAVs and real logs

Read `docs/AUDIO_LOG_CONTRACT.md`. Supported audio is 16-kHz mono PCM16 or float WAV with samples in [-1,1]. Convert other formats explicitly and record the conversion; the adapter never silently resamples or combines stereo channels. It processes nonoverlapping one-second windows and reports discarded partial-tail samples. Use actual recording start timestamps and canonical machine IDs.

Create normal training and calibration manifests plus a strictly later test manifest. Train a new baseline per actual machine/microphone setup, rather than applying generated-tone weights. Training content hashes must be distinct from calibration and inference content; same-asset recordings must not overlap. Thresholds and scalers never use test recordings. The caller must verify that training/calibration recordings are normal.

## Model and interpretation

Feature vector: time mean and standard deviation of 32 triangular log-mel bands, covering 40–7600 Hz. Features are standardized using training statistics, with a scale floor. Network: 64 → 32 → 8 → 32 → 64, ReLU, MSE reconstruction loss and Adam. Calibration's 99th-percentile reconstruction error becomes a frozen threshold. Gain, microphone position, noise and operating state can all change reconstruction error; it does not establish a mechanical cause.

Model state is saved as tensors/primitives and loaded with `weights_only=True`. Only use locally trained or otherwise trusted models. Event confidence=1.0 is an adapter acceptance marker for a computed/parsed observation, not a calibrated probability of failure. Source scores live in audio evidence. Step4 commands enable additional standalone acoustic and operational warning categories; legacy Steps1–3 keep their earlier report behavior by default.

There is no speech recognition, pretrained AudioSet classifier, live microphone streaming or root-cause diagnosis in this milestone. The actual signal-processing/DL pipeline is present, but real machine validation still requires licensed recordings, realistic background variation and independent labels.

## Included artifacts

| Item | Purpose |
|---|---|
| data/audio-demo/*.wav | Seven generated recordings: 48 train seconds, 24 calibration seconds, 30 test seconds |
| train.json, validation.json, test.json | Source manifests without fault labels |
| labels.json | Two separate labelled synthetic deviation intervals |
| logs.csv | Six synthetic controller records |
| source.json | Generator, seed and provenance |
| reports/step4_demo_summary.json | Verified run counts and synthetic evaluation |
| docs/STEP4_VALIDATION.md | Checks and limits |
| docs/STEP3_README.md | Previous sensor instructions |
| docs/STEP2_README.md | Previous video instructions |

To regenerate another seed, choose a new output folder:

```powershell
conda run -n opsfusion312 python -m opsfusion.audio demo --seed 43 --output .\data\audio-seed43
```

PyTorch model serialization reference: https://docs.pytorch.org/docs/stable/notes/serialization.html
SciPy WAV and STFT references: https://docs.scipy.org/doc/scipy/reference/io.html and https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.stft.html

Next: incident-level replay, independent labels, modality ablations, grounded reasoning and the dashboard. Real audio and video benchmarking should accompany those steps.
