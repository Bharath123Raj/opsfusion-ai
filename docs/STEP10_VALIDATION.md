# Step 10 validation — v0.10.0

Scope: accounts/roles, authenticated review/API, preview-first generic HTTPS
notification queue, in-place upgrade preservation, and existing perception/fusion
regressions. Tests used Python 3.12, CPU Torch 2.5.1, OpenCV 4.10, Ultralytics 8.3.40
and Streamlit 1.45.1 on Linux. Windows instructions reuse opsfusion312; hardware
camera and Windows in-place execution still need a local check.

## Software checks

**279 tests**, including 42 new checks for password/token hash storage, bootstrap,
roles, expiry/revocation, password-change form, account lockout, last-admin guard,
anonymous API/UI denial, bearer logout, actor binding, read-only controls, notice
snapshot freezing, deduplication, destination cursors, synthetic suppression,
attempt/retry limits, crash leases, changed endpoints, secret exclusion, actual
loopback HTTP POST, and upgrade preserve/block/rollback behavior.

The complete result is in reports/step10_software_validation.json. Software tests
use disposable databases, authored events and scripted failures. No user database
is bootstrapped or modified by dashboard tests. Historical fixture scores and
known segmentation limitations remain unchanged; these are not real accuracy.

## Browser integration

Playwright/Chromium exercised actual sign-in/sign-out, saved 26 authored reports,
made an authenticated review, downloaded history, built/exported 26 previews,
verified viewer controls were disabled, reloaded/re-authenticated, and checked
history persistence. It also started/stopped actual RTSP/YOLO capture through
the composed loopback server and reached 8 sampled frames before the Stop request.
Successful run: zero JavaScript errors/Streamlit exceptions and **zero external
notifications sent**. reports/step10_browser_validation.json records the scope.

Browser tools create fixture accounts only inside temporary project roots.
Credentials are test fixtures, not packaged production defaults. Optional
reproduction requires Playwright/Chromium and, for RTSP, FFmpeg plus existing
public sample/YOLO weight paths:

```powershell
conda run --no-capture-output -n opsfusion312 python tools/validate_live_browser.py
conda run --no-capture-output -n opsfusion312 python tools/validate_live_browser.py --rtsp-video .\videos\vtest.avi --weights .\yolov8n.pt
```

## Notification transport scope

Tests send actual HTTP JSON to a loopback receiver using an internal test-only
HTTP allowance. Production destination configuration requires HTTPS. Other
transport outcomes (503/429/redirect/timeouts) are scripted; capped backoff and
stable Idempotency-Key behavior are checked. No Slack/email service, remote
webhook provider, certificate/network failure environment or real recipient has
been configured/tested. Delivery is at least once; receiver deduplication is
required. HTTP 2xx does not prove human receipt.

## Upgrade scope and limits

The in-place upgrader preserves run data/evidence references, backs up old code,
blocks active workers and rolls back a scripted copying failure. It does not move
the user's profile, rename the project, or authenticate historical review labels.
Stop the existing Streamlit server before running it.

This is not an independent security audit, public-hosting readiness or proof of
real industrial incident accuracy. Local OS administration is trusted; no tenant
isolation, SSO/MFA, hosted private-media gateway, process supervisor or live
sensor/controller transport is implemented. Your controlled recording test is
deferred. Live cloud explanation acceptance remains unverified.
