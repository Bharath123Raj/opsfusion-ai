# OpsFusion AI — Step 16 (v0.16.0, free cloud deployment kit)

A local Streamlit application with opt-in camera/HTTP cooccurrence reports, HTTP sensor/controller ingestion, authenticated admin/operator/viewer accounts, a preview-first email/webhook queue, webcam/RTSP capture, saved history and audited review. It also runs the existing adapters on uploaded camera recordings, sensor measurements, machine audio and structured controller logs, then fuses their detections into incident review. It also supports existing event files, benchmarks, explanations and the event-level FastAPI service.

This cumulative package includes Steps 1–13 and Step15 deployment preparation; Step14 real recording validation is deferred. Extract Step16 separately. Begin with [START_HERE.md](START_HERE.md) and [the free cloud guide](docs/FREE_CLOUD_DEPLOYMENT.md). Reuse your `opsfusion312` environment for Windows commands. Private run folders and model weights are not copied; keep your previous installation.

Email remains optional; the local deployment preparation walkthrough uses dashboard alerts and requires no Gmail settings.

**Start here:** [Step 15 setup and deployment preparation](START_HERE.md). It includes an optional in-place upgrade that preserves your existing account, registered baselines, history, previews and evidence paths. [Accounts/notifications contract](docs/AUTH_NOTIFICATIONS.md) covers roles, bearer sessions and destination delivery. [Live capture and history contract](docs/LIVE_HISTORY.md) documents timing, reconnects, limits and CLI commands.

Step16 adds a stable cloud Compose project and host-side commands for configuration, build, migration/bootstrap, startup, health and backup export. It also closes SQLite backup connections before hashing, preventing later WAL checkpoints from invalidating archive checksums. Upgrade before making a migration backup. The selected free route uses Oracle Always Free ARM and DuckDNS, subject to eligibility, capacity and idle reclamation. Step15's protected gateway, durable storage and supervised services remain. See [cloud guide](docs/FREE_CLOUD_DEPLOYMENT.md), [deployment contract](docs/DEPLOYMENT.md) and [Step16 validation](docs/STEP16_VALIDATION.md). Actual VM provisioning, Docker builds and public HTTPS remain pending; no public deployment is claimed.

## 1. Install the dashboard/API dependencies

```powershell
conda run -n opsfusion312 python -m pip install -r requirements-app.txt -r requirements-vision.txt -r requirements-sensor.txt -r requirements-audio.txt
conda run -n opsfusion312 python -m unittest discover -s tests -v
```

Validation: 417 tests passed with no failures, errors or skips in the recorded Linux Python 3.12 run; see the validation report for limits. Python 3.12 is recommended. The app pins NumPy 1.26.4 and Arrow 17 to preserve compatibility with the earlier perception stack. Reuse the CPU torch 2.5.1 / torchvision 0.20.1 installation from Step 2.

## 2. Start the dashboard

Run this **inside the active project folder (fresh Step16 or your upgraded existing installation)**:

```powershell
conda run --no-capture-output -n opsfusion312 python -m streamlit run dashboard.py --server.port 8504
```

For a fresh installation only, create your first admin before starting: `conda run --no-capture-output -n opsfusion312 python -m opsfusion.accounts bootstrap --username bharath`. Choose your own 12+ character password at the hidden prompts. If you upgraded Step10, keep your existing account; do not bootstrap again. Open **http://127.0.0.1:8504** and sign in. Leave that terminal running. Press Ctrl+C to stop it. The dashboard does not need the API server running.

If upload shows `Tried to use SessionInfo before it was initialized`, see [upload/session recovery](docs/UPLOAD_TROUBLESHOOTING.md). This revision pins Streamlit 1.45.1; earlier packages pinned 1.40.2, predating documented session-initialization fixes.

**Real recordings is the default screen after an admin/operator signs in.** Viewers see saved history only. No synthetic incidents are loaded automatically. Upload files for the selected sources, supply their real recording times and asset IDs, then click **Analyse recordings and fuse incidents**. Detailed instructions: [raw-data integration](docs/REAL_DATA_INTEGRATION.md).

| Raw input | Processing inside the dashboard | Required setup |
|---|---|---|
| MP4/AVI/MOV/MKV video | YOLOv8n people → zone tracking → entry/presence observations | Recording start, asset/camera IDs, actual zone polygon |
| Sensor CSV | Per-asset Isolation Forest + robust residual/persistence rules | Normal training/calibration CSVs, or your trusted local sensor.joblib |
| 16-kHz mono WAV | Audio autoencoder scores → persisted acoustic episodes | Normal training/calibration WAVs and their start times, or your trusted local audio.pt |
| Controller log CSV | Explicit controller-code mapping → operational observations | CSV contract and known code mappings; no learned baseline |

You can analyse one source or several together. Sensor/log asset IDs and times come from their CSVs; they are never rewritten to make them match. Video/audio use the entered canonical asset ID. Only legitimately aligned sources should be treated as synchronized evidence.

To repeat your earlier actual-video run in the UI, upload the existing Step 2 `videos\vtest.avi`, retain the example zone for that public sample, use a documented test clock anchor and analyse 20 seconds at 5 sample FPS. This sample has no independently verified recording date: a chosen clock anchor is a demonstration, not proof of real synchronization. It yielded **100 sampled frames, 6 entry + 10 initial-presence observations**, and 2 episode first alerts here using actual YOLO inference. You can download the annotated detector video directly. To analyse your own camera recording, set its actual ground zone and known start time instead.

After a successful raw run, generated camera snapshots and acoustic/sensor episode evidence are linked automatically in **Incident evidence**. Outputs and newly trained models are saved to a unique `runs/raw-ingestion/...` folder. **Downloads** contains the review ZIP and a separate source-evidence ZIP with score tables, adapter manifests, evidence and newly trained models. Original large video/audio files are an explicit inclusion option.

For the earlier authored event benchmark, select **Synthetic incident demo** in the sidebar. It has **24 observations, 23 accepted observations, 26 first alerts** across all report categories. These are not 26 independently labelled physical faults.

Suggested first walkthrough:

1. In **Timeline**, filter an asset or move the UTC event-time cursor. Only this tab is filtered by the cursor.
2. In **Incident evidence**, select an incident. Choose `first` for its frozen first-alert event records or `final` for completed context.
3. In **Explanations**, review the same incident's possibilities, checks and cited event IDs. Every cause stays unconfirmed; checks never execute.
4. In **Benchmarks**, click **Run benchmark comparison**. The same events and labels score both engines. Nine ablations use the fixed engine.
5. In **Downloads**, download the review ZIP or individual JSON/HTML reports. Benchmark JSON and CSV are included in the ZIP if you computed them.

This Streamlit version can return to Timeline after a widget rerun. Reopen the relevant tab; the completed run and benchmark results remain available.

Expected development-fixture comparisons:

| Dataset | Engine | TP | FP | FN |
|---|---|---:|---:|---:|
| Incident demo | fixed | 7 | 0 | 1 |
| Incident demo | episode, gap 20s | 8 | 0 | 0 |
| Segmentation regression | fixed | 6 | 4 | 1 |
| Segmentation regression | episode, gap 20s | 6 | 1 | 1 |

Known authored fixtures are development checks, not blind validation. Metrics score declared target categories; all-category alert counts can be larger. The quiet gap is absence of adapter observations, not verified recovery.

## 3. Load your existing observations

Choose **Upload adapter events** in the sidebar and upload your earlier `combined_events.jsonl`, for example:

```text
Downloads\OpsFusion-AI-Step4\OpsFusion-AI-Step4\runs\four-source-demo-01\combined_events.jsonl
```

Choose engine/settings and click **Run replay**. Upload independent incident labels only if you have them. Generated incident reports cannot substitute for independent labels. Without labels, replay and explanations still work; benchmark metrics stay unavailable.

Limits: UTF-8 JSONL, 2 MiB, 200 input observations for review; 100 for interactive benchmarks. Duplicate identical IDs are deduplicated; conflicting IDs are rejected. Use the existing replay/benchmark CLIs for larger corpora. See [the event contract](docs/EVENT_CONTRACT.md) and [incident label contract](docs/INCIDENT_LABEL_CONTRACT.md).

Source observations marked synthetic keep that flag in every report and download. Your composed four-source demo includes public video plus generated sensor/audio data and synthetic logs. A source without the synthetic flag is not automatically verified as real or accurate.

The evidence viewer accepts explicitly uploaded PNG/JPEG, MP4 and WAV files up to 25 MiB. Evidence paths remain visible as metadata; the event-level API never reads or fetches them. Raw/live dashboards preview only evidence generated inside their own run folders. Manually uploaded media is not automatically matched to an event. Earlier batch episode evidence may include later context even when viewing a first-alert snapshot.

Changing inputs does not replace a successful run until **Run replay** or **Analyse recordings and fuse incidents** succeeds. A visible warning identifies stale inputs. Every successful new run clears previous benchmark results. Failed raw runs keep a failed manifest and never appear as successful incidents.

## 4. Optional API in a separate terminal

Open a **second PowerShell terminal in the same active project folder** if you want to use the HTTP API:

```powershell
conda run --no-capture-output -n opsfusion312 python -m uvicorn opsfusion.api:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000/docs** for interactive request examples. The original review endpoints are stateless and use the dashboard replay/explanation service. The /v1/streams routes maintain registered-model sensor/controller streams in shared SQLite history; /v1/watches explicitly link active same-asset camera and HTTP sessions for new joint reports.

| Route | Operation |
|---|---|
| `GET /health` | Version and local-review mode |
| `GET /v1/contract` | Event fields and request limits |
| `POST /v1/replay` | Events → replay, first-scope explanations and provenance |
| `POST /v1/explain` | Recompute a replay and explain one incident in first/final scope |
| `POST /v1/benchmark` | Independent labels + events → engine comparison and nine ablations |
| `POST /v1/export` | Events → downloadable review ZIP |

API requests now require bearer authentication. Use `/auth/login` with a local account, pass the returned token in `Authorization: Bearer ...`, and revoke it with `/auth/logout`. See [the complete PowerShell example](docs/AUTH_NOTIFICATIONS.md#review-api). No passwords/tokens belong in saved reports. The event-review API remains bounded at 2 MiB/200 observations and never reads supplied evidence paths. HTTP stream routes accept raw declared measurements/controller records against registered baselines; see [stream contract](docs/HTTP_STREAMS.md). Keep loopback binding; authentication alone is not shared-hosting readiness.

## Explanation scope and remaining limits

Dashboard/API explanations are the offline deterministic catalogue with **zero live model calls**. Optional live catalogue selection and visible fallback counts remain available through the [Step 7 CLI](docs/STEP7_README.md); live cloud acceptance is still unverified. This app does not silently call a model or claim model-generated diagnosis.

The UI now runs video/sensor/audio inference and can train sensor/audio baselines from your uploaded normal data. Existing CLIs remain available. The original review API consumes adapter observations; the new stream routes accept declared raw sensor/controller records. Neither route accepts arbitrary model paths or raw-media uploads. Step9 adds a separate webcam/RTSP worker, bounded frame buffering, reconnects, SQLite history and audited review. Select **Live monitoring & history**, or save a completed run through **Downloads → Save this run to incident history**. Only explicitly saved runs enter history. Live timestamps are host receipt times; camera acquisition clocks and transport delay remain unmeasured. Step10 adds local login, role-gated actions, authenticated review actors and a generic HTTPS webhook outbox with durable delivery attempts. Step11 adds one-inbox SMTP alerts with mandatory verified TLS, a queued connection test and stable Message-ID/retry records. Use [email setup](docs/EMAIL_DELIVERY.md); SMTP 250 is server acceptance, not guaranteed inbox receipt. Step12 adds authenticated, persisted HTTP sensor/controller streams, source/receipt clocks, idempotent inputs and dashboard evidence/alerts. Step13 adds explicit camera/HTTP source links and fixed camera-anchored cooccurrence reports with frozen first snapshots, gap resets, exports and a marked 18-second integration fixture. See [linked source contract](docs/LINKED_SOURCES.md). Step15 provides gateway-protected hosted media and persistent deployment templates, which require the private-backend topology documented in DEPLOYMENT.md. MQTT/OPC UA/direct hardware drivers, multiple-camera/audio live correlation, live audio and machine actions remain unimplemented. Preview records never send; real destinations need explicit admin enablement and a separate worker. Browser replay state is temporary; saved history persists. Large streams, causal media slices and production limits need further work.

See [app/API contract](docs/APP_CONTRACT.md), [Step 8 validation](docs/STEP8_VALIDATION.md), [Step 9 validation](docs/STEP9_VALIDATION.md), [Step 10 validation](docs/STEP10_VALIDATION.md), [Step 11 validation](docs/STEP11_VALIDATION.md), [Step 12 validation](docs/STEP12_VALIDATION.md), [Step 13 validation](docs/STEP13_VALIDATION.md), [Step 15 validation](docs/STEP15_VALIDATION.md), and [next steps](docs/NEXT_STEPS.md).
