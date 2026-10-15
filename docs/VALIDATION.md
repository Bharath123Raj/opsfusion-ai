# Validation — Step 2

Verified on Linux, Python 3.12, CPU, Ultralytics 8.3.40, Torch 2.5.1+cpu, Torchvision 0.20.1+cpu, OpenCV 4.10.0 and NumPy 1.26.4. Windows instructions use the same package versions; the desktop zone picker still needs a local GUI check on the user's computer.

## Software checks

33 tests passed: existing fusion tests plus polygon geometry, outside-to-inside confirmation, initial presence, jitter, missing detection, track expiry, exit/re-entry, multi-person association and decoded video/evidence/fusion integration. The adapter integration fixture injects scripted detections and is explicitly synthetic. The neural detector is exercised separately below.

## Actual neural inference smoke run

Source: OpenCV's `samples/data/vtest.avi`, downloaded directly from the OpenCV repository. The demo rectangle in `config/zone.example.json` is an arbitrary ground area, not a verified access-control boundary. The assigned UTC start time is a replay anchor, not the source recording's verified timestamp.

| Item | Observed result |
|---|---|
| Clip portion | First 20 seconds |
| Source FPS / decoded frames | 10 / 200 |
| Inference sample FPS / sampled frames | 5 / 100 |
| Model | COCO-pretrained yolov8n.pt |
| Detector source-score threshold | 0.6 |
| Entry observations | 6 |
| Initial-presence observations | 10 |
| Incident categories | restricted_zone; restricted_zone_presence |
| Aggregated incident reports | 2 |
| Max simultaneous person detections | 8 |
| Wall time (excluding model initialization) | About 6.80 seconds on this runtime |
| Mean detector time / p95 | About 62.71 / 62.35 ms |

Mean can exceed p95 when initialization outliers are above the 95th percentile. Timings are specific to this machine and are not Windows CPU promises or live alert latency. Output MP4 decoded successfully with all 100 expected frames. An entry snapshot was visually checked for the polygon, boxes and bottom-center points.

The reports group same-asset observations into the existing 90-second bounded windows. Six entry observations do not mean six separately verified safety incidents. Ten presence observations can include split tracks, newly visible people and people initially inside. This smoke run has no independently annotated ground truth, so precision, recall and safety accuracy are intentionally not reported.

Video SHA-256: `45cddc9490be69345cbdab64ca583be65987e864ca408038e648db99e10516cf`

Weights SHA-256: `f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36`

Weights source: https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt

Raw public video and weights are not included in the project ZIP. Follow README commands to reproduce the smoke run. Future labelled entry evaluation must match predicted track transitions to human-labelled temporal intervals, including no-entry clips, occlusion and crowded scenes.
