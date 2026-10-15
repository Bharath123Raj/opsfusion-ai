Step15 update: the optional authenticated gateway now gates Streamlit/media
traffic when the backend remains private. Persistent data, supervision and
backup/restore are documented in [DEPLOYMENT.md](DEPLOYMENT.md). Direct local
8504 access retains its earlier limitations; all accounts still share one site.

# Step12 addition

Admin/operator accounts can open, feed and close HTTP streams using registered
baselines. Only admins register models via the local CLI; viewers can read stream
status/evidence. See [HTTP_STREAMS.md](HTTP_STREAMS.md). Existing event-review
endpoints remain stateless; new stream endpoints persist data. Email is optional.

# Accounts and notification contract — v0.11.0

Email delivery is now included. See [EMAIL_DELIVERY.md](EMAIL_DELIVERY.md) for
SMTP configuration, tests, retry/receipt semantics and credential setup. The
HTTPS-specific settings below still apply to webhook destinations.

This is a single-computer/site milestone. Keep Streamlit and FastAPI bound to
127.0.0.1. Authentication does not make the application ready for public hosting.

## Accounts and roles

The first admin is created explicitly through the local CLI; no default password,
public registration route or bootstrap HTTP endpoint exists. Passwords have
12–256 characters and use PBKDF2-HMAC-SHA256, 600,000 iterations, random 16-byte
salts and constant-time verification. PBKDF2 is used for standard-library
portability; no FIPS certification or independent security audit is claimed.

Bearer sessions have 256 bits of random token input and an eight-hour maximum
lifetime. Only token hashes are stored. Sign-out revokes a token; password changes
revoke all of that user's tokens. Role/enable changes also revoke sessions.
Five failed attempts for a username within five minutes block further sign-in
until that window expires. Errors do not distinguish nonexistent/disabled users.
This local account throttle is not a distributed network abuse-prevention service.

| Action | Admin | Operator | Viewer |
|---|---|---|---|
| Read shared site history/evidence/downloads | Yes | Yes | Yes |
| Analyse recordings and save completed runs | Yes | Yes | No |
| Load an existing trusted local model file | Yes | No | No |
| Start/stop/recover local camera workers | Yes | Yes | No |
| Review incidents/dismiss local notices | Yes | Yes | No |
| Configure destinations/build queue/run delivery worker | Yes | No | No |
| Create/deactivate/change account roles | Yes | No | No |

Permissions are checked on the server before mutations. Operators can train baselines
from uploaded normal data; existing local model deserialization is admin-only. New reviews bind actor
to the authenticated username and record `actor_origin=authenticated_account`.
Old review labels are retained as `legacy_label`, never retroactively authenticated.
Locally created usernames are not externally verified employee identities.
All accounts share this installation's site data; no tenant/site isolation exists.

Browser reload loses the Streamlit login session and requires sign-in again.
History remains in SQLite. Expired/revoked tokens stop fresh reads/actions; the
live status fragment checks every two seconds. Already viewed/downloaded bytes
cannot be revoked. Streamlit media/download URLs are not per-user authorization
boundaries. Step15 supplies a protected reverse-proxy topology; it must be used for hosted media. Direct backend access must remain private.

Local OS/filesystem administrators and existing detector CLIs remain trusted.
CLI camera workers are not remotely authenticated services. Signing out does not
cancel an already running camera job; its Stop/duration controls remain in force.
Account hashes are in runs/accounts.sqlite3; never share that file or run backups
publicly. OS ACLs, TLS/SSO/MFA, secret management, recovery, tenant isolation and
deployment supervision remain production work.

## Setup and account management

Inside the running project folder, using the existing Python 3.12 environment:

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.accounts bootstrap --username bharath
```

Choose your own password at the hidden prompts; there is no password to copy
from this document. Use **Change my password** in the dashboard sidebar to change
your password and sign out all sessions.

Add an operator or viewer (prompts for admin and new account passwords):

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.accounts add-user --admin bharath --username reviewer --role operator
conda run --no-capture-output -n opsfusion312 python -m opsfusion.accounts add-user --admin bharath --username observer --role viewer
```

Change role or disable a user:

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.accounts set-user --admin bharath --username observer --role operator
conda run --no-capture-output -n opsfusion312 python -m opsfusion.accounts set-user --admin bharath --username observer --role viewer --disable
```

The last enabled admin cannot be disabled/demoted. No email password-recovery
service is included; retain your admin password and private profile backups.

## Review API

`/health` and API documentation are public metadata. All `/v1/*` review endpoints
require a valid bearer token; they remain stateless, bounded event processing
with no raw model/camera/path access. Auth routes use the same accounts database
as the dashboard when launched inside the same project folder.

```powershell
conda run --no-capture-output -n opsfusion312 python -m uvicorn opsfusion.api:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```powershell
$opsfusionPassword = Read-Host 'OpsFusion password' -AsSecureString
$opsfusionLoginBody = @{ username = 'bharath'; password = [System.Net.NetworkCredential]::new('', $opsfusionPassword).Password } | ConvertTo-Json
$opsfusionLogin = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/auth/login' -Method Post -ContentType 'application/json' -Body $opsfusionLoginBody
$opsfusionHeaders = @{ Authorization = 'Bearer ' + $opsfusionLogin.access_token }
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/v1/contract' -Headers $opsfusionHeaders
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/auth/logout' -Method Post -Headers $opsfusionHeaders
Remove-Variable opsfusionPassword, opsfusionLoginBody, opsfusionLogin, opsfusionHeaders
```

Do not paste tokens/passwords into reports. API login responses use no-store.
Viewer tokens can use stateless read/replay operations on supplied event data;
they cannot operate camera/review/notification mutations in the dashboard.

## Preview-first notification queue

Every first alert and reopening now stores its frozen report snapshot alongside
the local notice. Historical first notices can be reconstructed from frozen first
reports. Historical reopened notices without an exact snapshot are suppressed
rather than enriched with later evidence.

The reserved **Preview** destination exists by default and never sends. The admin
button **Build notification previews and queue** scans up to 100 new notices per
destination, stores immutable payloads and advances durable cursors. Repeat for
larger backlogs. The UI shows the latest 100 records; export includes all records.
Preview payloads contain incident ID/asset/type/priority/detected time, synthetic
flag, unconfirmed cause status and event IDs. No raw media/local file references,
passwords, bearer tokens, endpoint URLs or response bodies are included.

Real delivery requires an admin to configure and explicitly enable a destination
and run the separate notification worker. A destination accepts **new notices
after activation**; old notices are not automatically sent. Synthetic sources
are blocked unless an admin explicitly allows them for that destination.

Destinations use a named OPSFUSION_ environment variable holding a valid HTTPS
URL. The real URL is not stored in SQLite or process arguments. Credentials may
be part of the endpoint path/query but user-info and fragments are rejected.
TLS verification is on; redirects and inherited HTTP proxies are disabled.
This is a generic JSON webhook adapter, not a native Slack/Discord adapter; email uses the separate SMTP adapter.
The receiver must accept the documented JSON and implement its own downstream
notification format if needed.

To configure a real receiver later, supply the URL in the terminal that will
start the dashboard, before launching Streamlit:

```powershell
$opsfusionWebhook = Read-Host 'Your HTTPS webhook URL' -AsSecureString
$env:OPSFUSION_WEBHOOK_URL = [System.Net.NetworkCredential]::new('', $opsfusionWebhook).Password
conda run --no-capture-output -n opsfusion312 python -m streamlit run dashboard.py --server.port 8504
```

In **Live monitoring & history → Admin · notification destination**, choose a
name/environment variable and explicitly enable new deliveries. Then, in a
separate terminal, set the **same URL environment variable** again and run:

```powershell
$opsfusionWebhook = Read-Host 'The same HTTPS webhook URL' -AsSecureString
$env:OPSFUSION_WEBHOOK_URL = [System.Net.NetworkCredential]::new('', $opsfusionWebhook).Password
conda run --no-capture-output -n opsfusion312 python -m opsfusion.notify work --username bharath --max-seconds 3600
Remove-Item Env:\OPSFUSION_WEBHOOK_URL
Remove-Variable opsfusionWebhook
```

The worker prompts for the admin password and holds a revocable session in memory.
It stops on Ctrl+C, configured duration or lost admin authorization. It syncs
notices and processes due records; no network work runs in the dashboard UI.

Queue building without dispatch:

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.notify preview --username bharath
```

## Delivery and failure semantics

| State | Meaning |
|---|---|
| preview | Stored for inspection; never sent |
| pending | Awaiting a due attempt/retry |
| in_flight | Claimed by a worker with a 60-second lease |
| delivered | HTTP 2xx or SMTP acceptance, not proof of inbox placement/human receipt |
| failed | Permanent rejection/configuration issue or retry limit reached |
| suppressed | Synthetic source blocked or legacy snapshot unavailable |
| cancelled | Destination settings changed/paused before queued work completed |

Every attempt has a durable status/error record. Idempotency keys are also scoped
to a persistent installation ID to avoid collisions between local profiles. Transport errors, HTTP 429 and
HTTP 5xx retry at 5, 10, 20 seconds, with four total attempts. Other HTTP failures
and redirects are permanent. The client uses five-second network timeouts; OS
DNS and process stalls are not a hard overall deadline. A crashed/expired lease
can be reclaimed and is marked delivery_uncertain. Persistent failures need
operator investigation; there is no unlimited automatic retry or automatic resend
of failed backlog after changing a destination.

Delivery is **at least once**. Each logical notice/destination revision has a
stable Idempotency-Key. A receiver must deduplicate it; a crash after acceptance
but before local commit can otherwise duplicate side effects. Changing an endpoint
environment value without reconfiguration fails old deliveries as endpoint_changed.
Changing/pausing destination settings cancels pending/in-flight records; a request
already in transit cannot be recalled. Delivery/configuration records remain.

Local tests exercise HTTP transport, retries and role denial. TLS SMTP transport is tested with an actual local listener. No external
provider/destination has been configured or tested by this build.

## Primary references

- [OWASP password storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
- [Python hashlib PBKDF2](https://docs.python.org/3.12/library/hashlib.html#hashlib.pbkdf2_hmac)
- [HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/)
