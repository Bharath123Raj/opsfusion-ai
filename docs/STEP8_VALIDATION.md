# Step 8 validation — v0.8.2

Validation environment: Linux, Python 3.12, CPU. Windows PowerShell commands are provided for the user's existing opsfusion312 environment; they were not executed on a Windows machine here.

v0.8.2 upgrades the Streamlit pin from 1.40.2 to 1.45.1, retaining NumPy 1.26.4 and Arrow 17. The full software suite is rerun on this stack. Healthy-session browser upload/reload checks are recorded in step8_upload_session_validation.json. This does not reproduce or fully diagnose the user's Windows upload/session failure; [recovery instructions](UPLOAD_TROUBLESHOOTING.md) identify the documented dependency fix and a fresh-session restart.

## Automated checks

205 unittest tests pass, with no skips: 155 Step 7 checks, 33 review/API checks and 17 raw-integration checks. Review tests exercise API/shared-service equality across both engines and integer/float settings, strict event validation, conflicting IDs, request limits including chunked bodies, first-alert citation timing, explicit final scope, ZIP reproduction, source HTML escaping, known benchmarks, invalid labels, empty/low-confidence input, sidebar state, benchmark invalidation, missing uploads and mixed timestamp precision.

Raw tests exercise all four adapters from uploaded file bytes, sensor/audio model training and scoring, generated evidence linking, optional original-media exports, source/model synthetic propagation, trusted local model reuse, missing-baseline errors without demo fallback, recording time/zone validation, unknown log codes, filename/path boundaries and failed manifests. The dashboard now starts on raw uploads with no invented results. Training/inference tests use labelled synthetic fixtures and scripted test detections; they are software integration checks, not real fault accuracy measurements.

FastAPI TestClient exercises the actual HTTP app and OpenAPI route schemas. Streamlit AppTest runs the dashboard script and interacts with its widgets, rather than only testing its service functions. See `tests/test_app.py`.

The app dependencies pin NumPy 1.26.4 and pyarrow 17.0.0. An unbounded Arrow installation selected a version requiring NumPy 2 and failed rendering; the compatible pinned stack passed the dashboard tests.

Mixed whole-second and fractional-second timestamps occur in the supplied composed run. The timeline uses validated ISO strings in its temporal chart encoding and has a regression test covering this case. API coercion of numerical inputs does not change canonical fixed/episode replay identities across the dashboard and API.

## Behavioral results

| Corpus | Observations | Fixed TP/FP/FN | Episode TP/FP/FN, gap 20s |
|---|---:|---|---|
| Authored incident demo | 24 | 7 / 0 / 1 | 8 / 0 / 0 |
| Segmentation regression | 49 | 6 / 4 / 1 | 6 / 1 / 1 |

The first demo has 23 observations above the existing quality threshold and 26 first alerts across all categories. Independent labels score only their declared target categories; ignored categories remain visible in metric details. The known regression failures remain present.

These are deterministic downstream fusion results on authored adapter observations. They do not validate raw video/audio/sensor perception, live monitoring, physical diagnosis, clock synchronization, deployment scale or an independent holdout.

## Browser and HTTP smoke checks

The historical v0.8.0 `reports/step8_browser_validation.json` records browser and live loopback HTTP checks for event review. Chromium rendered that dashboard, ran the benchmark button, downloaded and opened an exported review ZIP, uploaded the earlier composed 26-observation event set, previewed a generated audio evidence file, selected final-scope explanations, checked that unlabelled inputs have no metrics, and confirmed a malformed upload preserved the previous successful run. The v0.8.1 raw browser check is recorded separately in `reports/step8_raw_browser_validation.json`.

The composed source set yields 11 first alerts and 11 final reports with the episode engine. It mixes public video and synthetic sources; those counts are integration results, not real-data accuracy metrics. Only count summaries are bundled from that check; the uploaded user-specific event records and paths are not repackaged.

Live Uvicorn checks include the replay endpoint, interactive API docs and a chunked body over the 2 MiB limit returning 413. Browser JavaScript error counts are recorded in the smoke-check JSON. Browser screenshots used during internal inspection are not delivery artifacts.

## Actual decoded video, v0.8.1

The raw-upload orchestration executed actual YOLOv8n CPU inference on 20 seconds of the public OpenCV vtest.avi sample: 100 sampled frames, 16 video observations (6 entries and 10 initial-presence observations), 2 episode first alerts and 16 generated evidence records. No detector was mocked or injected for this check. Source and official weight hashes are recorded in `reports/step8_real_video_validation.json`.

The public recording is actual image data; its original capture date was not independently verified. A test clock anchor was supplied solely to exercise timestamp conversion. No person/entry ground-truth labels were supplied, so these counts do not establish accuracy or real synchronization with industrial signals.

The revised browser workflow uploads actual public video, previews the selected zone, runs the normal YOLO detector and a synthetic controller fixture, opens generated evidence without manual reupload, and downloads the source ZIP containing annotated video, snapshots and adapter manifests. The log fixture and assigned test clock are disclosed explicitly; this is not a real synchronized industrial incident.

## Explanation and action scope

All app/API explanations are deterministic catalogue reports. Live model calls: zero. Causes stay unconfirmed and actions remain recommendations. Structural citation validation checks evidence IDs and compatible categories; it does not establish source truth or causal accuracy. Optional Step 7 live-provider selection remains available via the CLI and is still unverified against a real provider account.

The API is stateless, has no authentication and is intended for documented loopback use. The local dashboard now invokes the existing adapters for raw-file training/inference; those CLIs remain available. No live camera/sensor streaming, durable shared incident workflow or public deployment is claimed. The operator must supply real recordings, correct asset/unit mappings and independently reviewed normal baselines to evaluate their actual machines.
