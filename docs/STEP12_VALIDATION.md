# Step12 validation — v0.12.0

Scope: registered-baseline raw HTTP sensor/controller ingestion, saved incremental
state, atomic/idempotent requests, authenticated roles and visible local history/alerts.

## Software

345 tests passed, zero failures/errors/skips with Python 3.12.14, CPU Torch 2.5.1,
OpenCV 4.10, NumPy 1.26.4, sklearn 1.5.2, FastAPI 0.115.5 and Streamlit 1.45.1 on Linux.
The 38 new stream checks cover causal three-sample confirmation, equality with batch
candidate/alert rules, no repeated continuous-fault event, restart persistence,
identical/conflicting/concurrent retries, gaps/normal/spike behavior, unit/value/time
rejection, clock windows/order, synthetic propagation, asset isolation, controller
mapping/corroboration, frozen first evidence, capacity/write rollback, model hash
checks/immutability, source versus receipt clocks, secret exclusion, role/revocation,
HTTP body validation, registered-model restrictions, profile upgrade/no-op behavior
and dashboard evidence/close/local-alert controls.

Historical audio/video/sensor/fusion/account/queue/software regressions also pass.
The tests use disposable databases/models and generated/authored data; they do not
modify the user's actual profile or measure hardware/real detection accuracy.
Result: reports/step12_software_validation.json.

```powershell
conda run -n opsfusion312 python -m unittest discover -s tests -v
```

## Actual local HTTP and browser

A real Uvicorn API subprocess, a separately authenticated paced sender CLI and
Streamlit/Chromium exercised the full path. The 35-second generated fixture produced:

| Evidence | Count |
|---|---:|
| Accepted raw sensor readings | 35 |
| Authored controller records | 3 |
| Derived observations | 5 |
| Saved rule-based incident categories | 4 |
| Local first-alert notices | 4 |
| Exported raw input records | 38 |
| External notifications sent | 0 |

The browser watched input counts during acquisition, displayed local unread alerts
without any camera session, downloaded saved stream evidence, inspected the sensor
confirmation inputs, reloaded/re-authenticated and retained history. Viewer review
controls were disabled. All report causes remain unconfirmed and all evidence
retains synthetic provenance. No JavaScript errors/Streamlit exceptions occurred.
Result: reports/step12_stream_browser_validation.json.

Optional development reproduction needs Playwright and installed Chromium:

```powershell
conda run --no-capture-output -n opsfusion312 python tools/validate_stream_browser.py
```

Browser fixture accounts/passwords exist only inside temporary roots. The producer
uses its own local bearer session and never puts credentials in process arguments,
saved evidence or reports. Fixture models are created at run time and not bundled.

## Boundaries

No physical sensor/controller, MQTT/OPC UA/serial driver, calibrated acquisition
clock, real LAN deployment or hardware latency/accuracy was verified. The test
checks actual local transport/model/history behavior with generated data. Counts
refer to software rule reports, not independent real faults. The existing camera
and HTTP sessions still fuse separately; only same-asset HTTP sensor/controller
signals are jointly fused here. The controlled real-video test remains deferred.

SMTP is unchanged and optional. The user's Gmail setup was deferred; no successful
remote email delivery is claimed. Streamlit remains loopback, shared-site and without
per-user media authorization. No independent security/production readiness audit.

Windows PowerShell instructions preserve the active upgraded Step10 directory and
account/history paths. Actual Windows execution remains the user's local check.
