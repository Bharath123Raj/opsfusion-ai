Step15 update: the optional authenticated gateway now gates Streamlit/media
traffic when the backend remains private. Persistent data, supervision and
backup/restore are documented in [DEPLOYMENT.md](DEPLOYMENT.md). Direct local
8504 access retains its earlier limitations; all accounts still share one site.

Step13 update: opt-in active camera/HTTP source links now produce bounded joint
cooccurrence reports. See [LINKED_SOURCES.md](LINKED_SOURCES.md) for current
linking, clocks, gap boundaries and first/current evidence rules. Earlier
session-scoped fusion below remains available and keeps its existing reports.

# Live camera and persistent history contract — v0.9.0

This records the Step9 capture/history behavior. In Step10, dashboard access and
mutations require accounts/roles; new review actors are authenticated and old
labels remain legacy_label. See [AUTH_NOTIFICATIONS.md](AUTH_NOTIFICATIONS.md).
Camera worker CLI and timestamp/segmentation limits below remain unchanged.

## Capture lifecycle

Streamlit launches a separate worker, which loads CPU YOLOv8n and spawns an
isolated OpenCV capture process. A bounded two-message queue favors recent frames
rather than an accumulating backlog. Parent polling and child termination keep
blocking device reads out of the UI process.

Webcams use device indexes 0–20. RTSP requires an rtsp/rtsps URL with a hostname.
RTSP uses FFmpeg with 5-second open/read timeout options; the worker also watches
12 seconds without frames. OpenCV documents these as open-only FFmpeg/GStreamer
options, not universal driver guarantees. Native capture stderr is suppressed
to avoid leaking credentials; diagnostics use codes rather than exception text.

Reconnect delays are 1, 2, 4, 8, 8 seconds, with five consecutive retries after
failure. A successful frame resets the consecutive-failure counter. Reconnect
resets track identity and fusion scope. A person found inside after reconnect
creates presence, never a crossing inferred across missing footage. Starting a
new session after restart also breaks continuity.

Each camera ID has one active session in the shared SQLite database, and each
session can be claimed by one worker process. Separate installations/databases
do not share that lease. Stop is polled around capture and inference; model
loading/inference itself is not forcibly interrupted. Capture is stopped separately.

Default duration is one hour, selectable 1–86400 seconds. Capacity is 5000
observations/session and 200/active episode. Reaching capacity visibly stops the
worker and preserves committed history rather than silently truncating it.
No automatic evidence deletion is implemented; monitor disk space for extended use.

## Time, perception and evidence

Live UTC time is the host's frame receipt time, captured before inference. It is
not camera acquisition time, a synchronization measurement, or transport delay.
The host clock must be correct. Backwards/repeated event times fail ingestion;
equal-time observations must arrive in one batch. Monotonic time drives tracking
and sampling. Frames wider than 1280 pixels are resized before inference.

Target sampling is 1–10 FPS; achieved rate depends on delivery and CPU speed.
Frames older than two seconds are discarded. Conservative track expiry can
turn a crossing into initial presence when sampling is sparse. Greedy IoU IDs
can split/swap under occlusion. Track counts are not physical-person counts;
confidence is not entry accuracy.

Confirmed outside/inside transitions use the existing tracker and confidence
≥0.6. Continuous occupancy does not repeatedly alert. The 20-second quiet gap
is absence of observations, not verified departure/recovery. Live safety fusion
is independent per session. Step13 explicit camera/HTTP watches add bounded joint reports without changing those source reports; multi-camera fusion remains unimplemented.

Evidence is an annotated JPEG at the observation frame, written before its
report commits. `latest.jpg` is an atomic, overwritten sampled preview, not
continuous recording. Audio capture and live MP4 retention are not implemented.
Presence and entry are distinct report types. Causes/access authorization remain
unconfirmed; priority is not a calibrated safety rating. No machine action runs.

Generated/composed sources must be marked synthetic. Injected software detectors
also force synthetic provenance. Paced recording mode uses supplied recording
times, is labelled `file_replay`, and cannot establish live hardware accuracy.

## History and operator review

`runs/history.sqlite3` uses WAL transactions and a busy timeout. Events, immutable
first reports, current reports, reviews and local notices persist across browser/
dashboard restarts. Snapshots live in `runs/live/<session>/evidence`. Keep the
complete runs folder. Stop workers and the dashboard before a manual backup;
copying only the SQLite main file while WAL writers run is not a safe backup.

**Downloads → Save this run to incident history** saves completed replay/raw
runs idempotently for the same observations/settings and preserves replay first
snapshots. Distinct sessions stay separate; imports are not live multimodal fusion.

Review states: new, acknowledged, resolved, false_alarm. Operator labels are local
and unauthenticated. Resolving closes a review item, not a physical condition.
Decisions append audit records and never alter detector facts. New evidence after
resolved/false_alarm reopens the item and adds a notice; updates to acknowledged
reports retain acknowledgement. Revision checks reject stale operator decisions.

**Refresh history** loads a new review snapshot. Automatic two-second refresh
updates only worker status/previews/notices, keeping the reviewed snapshot stable.
UI lists show up to 100 records; active sessions sort first. JSON exports all
history and audit records with evidence references, not image bytes. Only images
inside this installation's runs/live or runs/raw-ingestion are auto-previewed.
Imported external paths remain metadata. References are not rewritten after moving
the project. Snapshot previews can remain visible after capture has stopped.

Notices are local records for first alerts/reopening, with dismissal. Email,
Slack, SMS and external delivery are not implemented. SQLite is local storage,
not an authenticated multi-tenant service. Keep the loopback server binding.

## CLI — inside the project folder

Webcam in a separate terminal (adjust the example zone for the scene):

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.live run --webcam 0 --zone .\config\zone.example.json --max-seconds 60
```

The worker prints a session ID. From another terminal:

```powershell
conda run -n opsfusion312 python -m opsfusion.live stop --session-id YOUR_PRINTED_SESSION_ID
```

RTSP credentials through process environment, without a saved file/command-line URL:

```powershell
$opsfusionCameraUrl = Read-Host 'RTSP URL' -AsSecureString
$env:OPSFUSION_RTSP_URL = [System.Net.NetworkCredential]::new('', $opsfusionCameraUrl).Password
conda run --no-capture-output -n opsfusion312 python -m opsfusion.live run --rtsp-env OPSFUSION_RTSP_URL --zone .\config\zone.example.json --max-seconds 60
Remove-Item Env:\OPSFUSION_RTSP_URL
```

This is local process configuration, not a secret vault. Do not paste credentials
into issue reports. The dashboard passes its password input to the worker in
memory; credentials are excluded from process arguments/history/reports.

Paced recording through the same worker:

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.live run --replay-video .\videos\vtest.avi --start-time '2026-01-01T10:00:00Z' --zone .\config\zone.example.json --max-seconds 20
```

January is an assigned demo clock, not the public sample's verified recording
date. This mode drops frames to maintain pace; counts are not an accuracy comparison
with offline processing. Model weights/sample videos are not bundled; the first
YOLO run may download official weights.

## Diagnostics

| Code/state | Meaning |
|---|---|
| starting | Model/camera initializing; initial weight download may take time |
| reconnecting | Missing footage; retry in progress and identities reset |
| camera_open_failed | Check device index/use by another app or RTSP URL/network/codec |
| camera_read_failed / capture_unresponsive | Stream/device stopped delivering |
| no_frames_received | No sampled frames; not a successful timed session |
| detector_or_processing_failed | Check vision dependencies, model access and writable runs folder |
| capacity_reached | Saved history retained; review and begin a new bounded session |
| clock_moved_backwards | Correct host clock and begin a new session |
| stopped | Operator Stop or Ctrl+C in the worker terminal |
| completed | Duration reached or paced recording ended, with at least one sampled frame |
| interrupted / stale_worker_recovered | Explicit recovery of a missing worker |

**Recover stale worker** requires a heartbeat at least 120 seconds old; recent
workers require Stop. Investigate before recovering a slow model-loading worker.
The displaced worker cannot ingest later. No automatic takeover runs.

## Primary references

- [OpenCV 4.10 video I/O properties](https://docs.opencv.org/4.10.0/d4/d15/group__videoio__flags__base.html)
- [Streamlit fragments](https://docs.streamlit.io/develop/concepts/architecture/fragments)
- [RFC 2326 §10.12](https://www.rfc-editor.org/rfc/rfc2326#section-10.12): RTSP/1.0 framing used by the optional loopback test server
