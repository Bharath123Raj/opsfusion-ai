# Step 9 validation — v0.9.0

This release adds live camera ingestion and persistent local incident review.
Validation runs used Python 3.12, CPU Torch 2.5.1, OpenCV 4.10.0.84,
Ultralytics 8.3.40 and Streamlit 1.45.1 on Linux. Windows spawn-compatible
process APIs are used; the user's Windows camera hardware remains untested.

## Software suite

**237 passed, 0 failures, 0 errors, 0 skipped.** Includes 32 new checks covering
database persistence, exclusive camera/session claims, immutable first reports,
current updates, equal-time batches, duplicate/conflicting events, source
isolation, quiet-gap/reconnect boundaries, capacity preservation, clock rollback,
stop/retry/model-failure lifecycle, synthetic provenance, credential exclusion,
safe evidence preview and operator revision conflicts/audit/reopening.

Tests use authored events/scripted detections where appropriate; they do not
measure learned-model accuracy. Prior fixture benchmark results and known
segmentation limitations are retained. Evidence: reports/step9_software_validation.json.

## Actual RTSP decode and YOLO integration

The optional loopback server sends the public OpenCV vtest recording as composed
RTSP/TCP with interleaved RTP/JPEG. The worker uses actual OpenCV decoding and
actual YOLOv8n inference. No injected detector is used. A forced disconnect is
followed by a new connection, with track/fusion reset and durable reports.

The retained check decoded **49 sampled frames**, saved **12 observations** and
**3 reports**, with two RTSP connections and one connection boundary. Duration
and CPU/drop timing affect counts; they are not accuracy targets. All footage
from this composed fixture is explicitly synthetic. Fixture credentials were
absent from history and worker output. Source/weights SHA256 and detector/settings
provenance are in reports/step9_rtsp_validation.json.

The first test server encoding was incompatible with RFC JPEG transport; it was
corrected to use standard Huffman tables. A zero-frame timed session now fails
visibly instead of reporting completion. No claim is made for hardware webcam,
production CCTV codecs, acquisition-clock alignment, or real perception accuracy.

To reproduce transport checks, install FFmpeg separately on PATH (optional test
dependency), then use actual existing file/model paths and a new output directory:

```powershell
conda run --no-capture-output -n opsfusion312 python tools/validate_rtsp.py --video .\videos\vtest.avi --model .\yolov8n.pt --output .\runs\rtsp-transport-check
```

## Browser review checks

Playwright/Chromium exercised a fresh dashboard root with the authored event demo:
save 26 reports, select first/current scope, acknowledge with operator note,
confirm audit, download history JSON and reload while preserving the review.
No JavaScript errors or Streamlit exceptions occurred in the successful run.
The pinned frontend can reset replay tabs after rerun; the harness accounts for
hidden-tab confirmation messages and uses visible checkbox labels.

History download avoids triggering a full rerun, keeping the generated download
stable. The executed browser RTSP check additionally started and stopped a real
worker using the composed loopback stream, reached 8 sampled frames before the
Stop request, and verified password-field clearing.
See the exact executed scope in reports/step9_browser_validation.json.

Browser validation tools require Playwright and Chromium separately; these are
optional developer checks, not dashboard runtime requirements:

```powershell
conda run -n opsfusion312 python -m pip install playwright==1.51.0
conda run -n opsfusion312 python -m playwright install chromium
conda run --no-capture-output -n opsfusion312 python tools/validate_live_browser.py
```

Optional actual worker check also needs FFmpeg and existing sample/weights:

```powershell
conda run --no-capture-output -n opsfusion312 python tools/validate_live_browser.py --rtsp-video .\videos\vtest.avi --weights .\yolov8n.pt
```

These developer commands use temporary data folders, a loopback-only server, and
composed footage. No external notification messages are sent. Hardware testing,
independent labels, false alarms/hour, misses, alert delay and real multimodal
alignment remain validation work.
