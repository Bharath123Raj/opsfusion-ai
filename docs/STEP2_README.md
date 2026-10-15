# OpsFusion AI — Step 2 (v0.2.0)

Actual video frames → pretrained YOLO person detector → heuristic tracking → polygon entry/presence observations → OpsFusion evidence graph and incident reports.

This cumulative package includes Step 1. Use this folder as your new project folder; your old Step-1 folder can stay intact. Requires Python 3.10–3.12; Python 3.11 is a good Windows choice. Inference runs locally on CPU by default. No paid API or GPU is required. Dependencies and first model download require internet.

## 1. Install on Windows PowerShell

Open PowerShell inside the extracted `OpsFusion-AI-Step2` folder. Use a separate environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements-vision.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: 33 tests pass. One test uses generated frames and scripted detections to verify media output and fusion; it does not test neural model accuracy. Explicit interpreter paths avoid PowerShell activation-policy issues. If Python is unavailable, install Python 3.11 and reopen the terminal, or substitute `py -3.11` for the initial `python` command.

## 2. Supply a short video

Place a fixed-camera clip at `videos/input.mp4`. Choose a clip where a visible person walks from outside to inside a region on the ground. Start with 20–60 seconds. Raw video and weights are not bundled.

Alternatively, download OpenCV's pedestrian sample for a reproducible technical smoke run:

```powershell
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/opencv/opencv/master/samples/data/vtest.avi" -OutFile .\videos\vtest.avi
```

For that sample, you may use `config/zone.example.json`, which is a demonstration rectangle. The sample is not a real restricted-zone incident dataset; its recording start is unknown. The example start time below is an assigned replay anchor, not a verified historical time. Source: https://github.com/opencv/opencv/blob/master/samples/data/vtest.avi

## 3. Draw your zone

For your own clip:

```powershell
.\.venv\Scripts\python.exe -m opsfusion.video pick-zone --video .\videos\input.mp4 --output .\config\zone.json --asset-id warehouse-zone-01 --camera-id camera-01
```

A window opens on the first frame. Click 3 or more vertices around the ground area in clockwise or counterclockwise order, then press Enter to save. R resets; Esc cancels. Avoid a crossed polygon. `--at 5` opens the frame at five seconds. Existing configs are protected; choose another output filename to redraw.

The person's bottom-center bounding-box point determines zone membership. Draw on the floor, not around the entire body. All coordinates are normalized, so a uniformly resized video preserves the zone. Camera movement or cropping requires recalibration.

## 4. Process actual video

```powershell
.\.venv\Scripts\python.exe -m opsfusion.video run --video .\videos\input.mp4 --zone .\config\zone.json --start-time "2026-10-12T10:00:00+05:30" --sample-fps 5 --max-seconds 30 --output .\runs\video-01
```

Replace the start time with the recording start including timezone. With an unknown start, use an explicitly assigned replay anchor and avoid combining it with real timestamped sensors. Omit `--max-seconds` to process the whole clip. Default model is `yolov8n.pt`, downloaded on first use; you can pass a local trusted `.pt` weights file using `--model`. Default CPU inference may run slower than the clip duration. Existing run folders are protected; use `video-02` for a rerun.

To run the OpenCV sample with the demo rectangle instead:

```powershell
.\.venv\Scripts\python.exe -m opsfusion.video run --video .\videos\vtest.avi --zone .\config\zone.example.json --start-time "2026-01-01T10:00:00Z" --max-seconds 20 --output .\runs\opencv-smoke-01
```

## 5. Review the results

```powershell
Start-Process .\runs\video-01\annotated.mp4
notepad .\runs\video-01\incidents.json
notepad .\runs\video-01\run_manifest.json
```

| Output | Meaning |
|---|---|
| annotated.mp4 | Sampled preview with polygon, person boxes, foot points, track IDs and time; no audio |
| evidence/*.jpg | Annotated snapshots at confirmed entry/presence observations |
| detections.jsonl | Per-sample boxes, source confidence, tracks, state and transitions |
| video_events.jsonl | Actual CV observations using the OpsFusion event contract |
| combined_events.jsonl | Video observations plus optional supplied events |
| incidents.json | Fused incidents, observations, hypotheses and graph |
| zone.json | Exact zone config used |
| run_manifest.json | Completion status, source hash, weights hash, sampling, timings and counts |

`entry` requires the same heuristic track to be confirmed outside, then confirmed inside, for two consecutive sampled detections each. No alert repeats during continuous occupancy. Exit then re-entry produces a new observation. A person first seen inside produces `zone_presence` and a `restricted_zone_presence` report, with entry time explicitly unknown. Missing detections reset the confirmation streak; expired tracks are new IDs. Tracking is geometric, not face recognition or persistent personal identity.

Observations are `synthetic: false` for ordinary videos. Use `--synthetic-video` for generated/composed clips. Actual-video processing does not merge the Step-1 synthetic scenarios automatically. To combine genuinely synchronized sources, assign the same asset ID and supply `--merge-events path/to/events.jsonl`. Fusion uses the existing 90-second bounded windows and a 0.6 minimum source score. Camera asset IDs should identify the monitored machine if correlating machine signals; zone-only IDs group safety evidence.

## Current limits

This milestone performs pretrained deep-learning inference, not custom model training. A polygon observation cannot determine whether a person is authorized. IoU tracking can split identities or swap nearby people, so crowding, occlusion and fast motion can miss entries or produce additional presence reports. Increase sample FPS for faster movement. At 5 FPS, two consecutive samples add about 0.2 seconds after the first inside sample before confirmation, plus inference time. This is offline replay and does not claim measured live alert latency.

Use constant-frame-rate video; timestamps derive from frame index/FPS. Convert variable-frame-rate phone video before temporal fusion. Risk labels remain configured rule priorities, not calibrated safety assessments. A correct smoke run is not a precision/recall benchmark; label independent clips before claiming accuracy. Empty incident reports are valid when no person is detected inside or crossing the chosen zone.

## Troubleshooting

- No entries: review `annotated.mp4`. Ensure a person's foot point crosses the polygon and the same ID is visible outside first. First-observed inside is presence, not entry.
- No person boxes: use better lighting and a larger, unobscured person; source threshold defaults to 0.6. Check model class names and detector visibility before blaming fusion.
- Missing imports: invoke `.venv\Scripts\python.exe` and install requirements there.
- Model download fails: download trusted `yolov8n.pt` through the official Ultralytics source and pass its local path.
- Zone picker fails on a remote/headless machine: supply a normalized polygon JSON; the run command does not need a GUI. Desktop Windows needs `opencv-python`, not its headless replacement.
- Rerun says folder exists: use a new output directory. A failed run has `status: failed` and partial artifacts must not be treated as complete.

## References and next milestone

Detector API: https://docs.ultralytics.com/modes/predict/
YOLOv8 weights: https://docs.ultralytics.com/models/yolov8/
Ultralytics license information: https://www.ultralytics.com/license

The adapter installs Ultralytics as a dependency; its models/package have their own license terms. Model weights and public video are not redistributed in this ZIP.

Next: sensor CSV inference with a time-based training/test split, followed by synchronized multimodal evaluation and labelled video entry benchmarking. See `docs/VALIDATION.md` for this package's verified checks.
