# Single-site deployment preparation

Step15 prepares deployment; Step16 selects/runs the actual host. No public site,
domain, external account or paid plan has been provisioned. Step14 real recording
validation remains pending. This is a single-site MVP with trusted OS admins,
not a tenant-isolated industrial service or an independent security audit.

## Protected topology

The supplied gateway is the only published service. Streamlit and the API stay
on a private container network. Caddy checks the account cookie through
/access/verify before forwarding EVERY Streamlit request, including /media,
downloads, uploads, static assets and the initial WebSocket handshake. Authentication
pre-checks strip WebSocket Upgrade/Connection headers from the cloned request;
the original WebSocket is forwarded normally only after successful authentication.

The /access/login page uses a same-origin form, random CSRF cookie/form nonce,
strict Origin comparison, bounded fields and the existing account password hash
and failed-login limits. The HTTPS browser session is an opaque 8-hour bearer in
a Secure/HttpOnly/SameSite=Lax __Host- cookie, checked against hashed database
sessions. Login replaces/revokes an earlier cookie session; logout requires a
same-origin CSRF form and revokes the bearer. Disabled/changed accounts and
password changes revoke sessions using the existing account rules. Browser
pages use no-store and a restricted CSP. No password/bearer is deliberately
written to reports or logs. Browser access introduces no default credentials.

Streamlit reads the cookie on the server through st.context and validates the
same account on each protected action/fragment. Existing WebSockets are not
forcefully disconnected by Caddy mid-connection; backend permission checks run
again and fragment refreshes detect revocation. Each new media/download HTTP
request and WebSocket handshake is gated immediately. Authorized users can keep
copies they already downloaded; logout cannot retract those copies.

All enabled account roles have site-wide read access to the same history/media.
This protects anonymous access, not per-user tenants. /v1 mutations continue to
require explicit Authorization: Bearer headers; a browser cookie cannot authorize
API operations. The API login endpoint remains available for acquisition clients.
Health and API contract documentation contain no account or evidence payloads.

The direct local 8504 workflow retains its earlier login/media limitations.
Publishing port 8504, exposing the private backend by another route, bypassing
the gateway, or copying only the Streamlit app is outside this protected topology.
Do not disable CORS, XSRF or TLS certificate verification for a hosted deployment.
Only the test harness accepts its disposable internal test certificate.

## Persistent data versus code

OPSFUSION_DATA_ROOT is an absolute directory. It holds runs/ accounts/history,
registered baselines, source evidence and models; immutable code/demo fixtures
stay in the package directory. API, dashboard and CLI tools resolve the same data
root. Without this environment variable, existing local folder behavior remains.
An explicit API project_root remains an override for isolated testing.

The container mounts /data on a named volume, uses a non-root application user,
and keeps the code filesystem read-only. The image provisions pinned CPU
dependencies and hash-verified YOLOv8n weights. Runtime caches go under /data/cache.
No secret environment file, existing account database, recorded media or model
artifact from the Windows installation is copied into the image build context.
The .dockerignore also excludes tests/tools, runtime databases, backups and logs.

Do not remove the named data volume when replacing/updating containers. Docker
Compose down without -v retains named volumes; down -v deletes declared volumes.
A named volume protects against container replacement, not host loss. Keep
independent private backups. Current templates assume a Docker host, WebSockets,
background processes and persistent storage; serverless-only hosting is unsuitable.

## Service supervision

python -m opsfusion.service starts API/dashboard and acquires an OS lock for its
data directory. It fails on occupied ports, checks both children, restarts the
pair with backoff after a crash or repeated health failure, and exits nonzero
after three restarts. The container's unless-stopped policy can restart the unit.
Ctrl+C/SIGTERM stops the two supervised children. An interrupted standalone source
worker/input client is not automatically declared healthy or physically recovered.

Camera workers and notification workers remain separate lifecycles. Stop camera
workers and close HTTP streams/watches before planned shutdown/backup. Lost camera
workers keep explicit stale recovery; HTTP streams can be closed in the UI. No
PID takeover, machine-control recovery or automatic evidence purge occurs.
service-state.json contains local process/restart diagnostics; it is not a secret
store and is excluded from backups. Container logs rotate at 10 MB, three files.

## Optional local Docker preview

compose.yaml publishes only 127.0.0.1:8080 and uses explicit local HTTP cookie mode.
It is a preview for localhost, not a public HTTP deployment. On a machine with
Docker, run from the package directory:

```text
docker compose build
docker compose run --rm app python -m opsfusion.accounts bootstrap --username YOUR_ADMIN
docker compose up -d
```

Bootstrap only a fresh empty volume. This is a new site; it does not magically
inherit Windows accounts. Open http://localhost:8080. For an existing volume,
keep its accounts or restore your trusted data first. Do not repeat bootstrap.
No SMTP environment or notification worker is necessary for dashboard alerts.

## Step16 HTTPS template

compose.https.yaml is a SEPARATE full configuration, not an override layered over
the loopback preview. Set OPSFUSION_DOMAIN to the exact DNS hostname, configure
the host firewall/DNS and use:

```text
docker compose -f compose.https.yaml build
docker compose -f compose.https.yaml run --rm app python -m opsfusion.accounts bootstrap --username YOUR_ADMIN
docker compose -f compose.https.yaml up -d
```

Caddy's public certificate provisioning needs the real reachable domain/ports
and is unverified here. The app still exposes no backend host ports. Certificate
state is kept in named Caddy volumes. The template has resource and rotating-log
limits, but no measured production throughput/DoS guarantee. The Docker image
build/start itself remains unverified because the build environment had no Docker
engine. Selecting a provider and running its acceptance checks is Step16/17 work.

A hosted server cannot access a user's laptop webcam. Reachable RTSP/network
sources need a site network arrangement; physical on-site acquisition/edge
protocol bridges are still pending. Uploaded recordings work through the existing
adapters. Synthetic input tests are not a real-camera accuracy measurement.

## Guarded offline backups and migration

Stop all standalone servers, camera/notification workers and input clients; close
streams/watches. --offline is an operator attestation that standalone processes
are stopped. The supervised service lock and active-session/in-flight checks
also enforce quiescence for known managed activity. The tool does not discover
arbitrary independently launched processes on the OS.

maintenance backup snapshots SQLite with the SQLite backup API and copies
generated evidence/models from the quiescent data root. It excludes transient
WAL/SHM/locks, code backups, environment files and caches. videos/ is opt-in.
Limits are 2 GiB total source data and 20,000 files. Backups contain account
password hashes, private media and measurements: keep them private, outside
GitHub, and access-controlled. They are not encrypted by this tool. Active account
sessions are removed from the copy; making a backup does not revoke source sessions.

Restore requires a trusted archive and a NEW/empty destination. It rejects unsafe
paths, duplicate/unlisted/symlink members, over-limit content, mismatched hashes
and invalid SQLite databases before promoting a staged directory. SHA256 verifies
content integrity, not who created the archive. Restore is a trusted local admin
operation; no HTTP backup/model-upload/restore route exists. Do not restore an
unknown downloaded model archive. A populated destination is never overwritten.

Account sessions are removed on restore. Original report payloads, first snapshots,
IDs, reviews and audit stay unchanged. A restore-origins registry maps old file
references only into runs/live or runs/raw-ingestion in the new root. The existing
preview extension/size/path allowlist still applies; arbitrary external paths and
URLs are never fetched. This lets evidence survive Windows-to-Linux directory
changes while preserving the original report facts. Code is not in the backup.

On Windows, run this version's code with:

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.service --data-root "C:\YOUR\NEW\RESTORED-DATA"
```

For a Docker migration, stop the stack and use a one-off app container on the same
named volume. Restore into an empty subdirectory such as /data/restored, then set
OPSFUSION_DATA_ROOT=/data/restored consistently in the app before starting. Mount
the private backup read-only for that one-off command; never COPY it into the
image. Paths/credentials depend on the actual Step16 host, so review the concrete
migration command there. Keep the original source installation and backup intact.

## Explicit backup retention

maintenance retention --folder DIR --keep 5 previews older recognized backup ZIPs.
--apply deletes only selected files matching the managed backup naming/manifest
contract. Other ZIPs remain. No automatic incident/history/evidence deletion is
enabled. Filesystem permissions, host backups and disk monitoring remain operator
responsibilities; the retention command is not a complete compliance policy.

## Sources checked during implementation

- [Caddy forward_auth](https://caddyserver.com/docs/caddyfile/directives/forward_auth)
- [Caddy 2.11.7 release](https://github.com/caddyserver/caddy/releases/tag/v2.11.7)
- [Streamlit 1.45 context cookies](https://docs.streamlit.io/1.45.0/develop/api-reference/caching-and-state/st.context)
- [Docker volumes](https://docs.docker.com/engine/storage/volumes/)
- [Browser Origin header behavior](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Origin)
