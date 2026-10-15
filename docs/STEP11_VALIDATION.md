# Step 11 validation — v0.11.0

Scope: TLS SMTP email adapter, durable test-email queue, role checks, secret
exclusion, in-place Step10 upgrade and existing application regressions.

## Software

307 tests passed, zero failures/errors/skips on Python 3.12/Linux with CPU Torch
2.5.1, NumPy 1.26.4, OpenCV 4.10 and Streamlit 1.45.1. The 28 new email checks
cover mandatory TLS-before-authentication, implicit TLS, bad certificate rejection,
single-recipient validation/header injection, frozen evidence-only contents,
synthetic suppression/demo markings, test probes without incidents, repeated-click
deduplication, stable Message-ID, 4xx retries/5xx permanent failures, credential and
recipient rejection, quit failure after acceptance, route-change blocking,
password rotation, account permissions and legacy-schema/in-place upgrade preservation.

Actual SMTP tests use local sockets with STARTTLS and implicit TLS; a public
localhost test certificate is explicitly trusted only inside test clients. Default
trust rejects that same untrusted certificate before authentication. Other provider
failures are scripted. No external email was sent. The full suite includes earlier
audio/sensor/video adapters and software vision/fusion fixtures; it is not camera
hardware or detection accuracy validation.

Result: reports/step11_software_validation.json. Reproduce after installing the
four existing requirements files:

```powershell
conda run -n opsfusion312 python -m unittest discover -s tests -v
```

## Actual browser and worker

Playwright exercised sign-in, email destination save and Queue test email without
UI network delivery. A separate actual notification-worker CLI authenticated with
a disposable account and submitted two messages to the local TLS SMTP listener:
one TEST email and one authored incident email. Both recorded SMTP 250 acceptance.
The browser refreshed/exported delivery records, checked credential/recipient
exclusion, reloaded/re-authenticated and verified persistence. A viewer could not
configure/test/queue. Zero JavaScript errors and zero external messages.

Result: reports/step11_email_browser_validation.json. The existing login/review/
26-preview/download/reload workflow was also rechecked:
reports/step11_browser_validation.json. That run did not repeat the optional
RTSP-camera check; historical Step9/Step10 camera proofs retain their version/scope.

Optional reproduction needs Playwright and Chromium in your development environment:

```powershell
conda run --no-capture-output -n opsfusion312 python tools/validate_email_browser.py
conda run --no-capture-output -n opsfusion312 python tools/validate_live_browser.py
```

## Limits

No real Gmail/other-provider account has been configured by these checks. Provider
SMTP policy, credentials, network access, spam filtering, inbox placement and
receipt need your own connection test. SMTP acceptance alone proves no human receipt.
Retries can duplicate accepted emails after a lost acknowledgement or worker crash.

Upgrade tests preserve existing account/hash/token bytes, history and queue records
and retain original folder/evidence paths. The upgraded release migrates queue
schemas additively. Windows PowerShell helper syntax/instructions are supplied;
actual Windows execution was not performed in this Linux environment.

This is not a security audit, hosted deployment proof, industrial sensor integration
or real-world incident accuracy measurement. The deferred controlled video test and
remaining roadmap work still apply.
