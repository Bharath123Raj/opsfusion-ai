# Netlify + Supabase + Python processing

The cloud portal handles uploaded recordings. The existing local Streamlit/API
application remains available separately. This deployment does not move its
SQLite accounts, old runs, camera watches or live sensor streams into the cloud.

## Components and authorization

- `web/`: a Vite/React portal for password sign-in, uploads, queued job progress,
  frozen/final incident reports, private evidence downloads and saved reviews.
- Supabase Auth, `opsfusion_*` tables and two **private** buckets. The applied
  migration is `supabase/migrations/20261015093226_opsfusion_cloud_foundation.sql`.
- `opsfusion.cloud_worker`: claims jobs atomically, validates size and SHA-256,
  runs the existing adapters and fusion, saves private artifacts, commits reports.
- This is one shared operational site: all enabled members can read site data;
  admin/operator roles can submit jobs and append review decisions. Viewer roles
  are read-only. Membership is managed by a trusted database administrator.
  It is not a multi-tenant SaaS authorization model.

Google sign-in is enabled on the selected Supabase project. The initial admin
uses that existing Google identity. Password sign-in is also supported for Auth
users that have configured passwords.

The browser uses a **publishable** API key. It cannot invoke worker functions,
edit reports, change memberships or replace submitted inputs. The worker uses a
separate backend secret key with project-wide privileges; isolate that host and
never put its key in GitHub source, browser variables, Netlify, or chat.

## Netlify

1. Sign in to Netlify and import `Bharath123Raj/opsfusion-ai`, branch `main`.
2. Use the repository's `netlify.toml`: base directory `web`, build command
   `npm ci && npm run build`, publish directory `dist` relative to that base.
3. The configuration includes this installation's public Supabase URL and
   publishable key. For another project, replace both and update the CSP host.
   Never substitute a secret/service-role key.
4. In Supabase **Authentication → URL Configuration**, add the published HTTPS
   origin to the allowed Redirect URLs. Configure the Site URL for this app,
   preserving any redirect entries used by other applications. Google must also
   allow the existing Supabase Auth callback in its OAuth client configuration.
5. Open the HTTPS site and choose **Continue with Google** using your enabled
   member email, or use a Supabase Auth email/password account. The old local
   `bharath` username/password is separate. This version does not send invitation
   or password-reset messages.

The `/* -> /index.html` redirect supports the single-page application. Security
headers disallow framing and limit requests to the configured Supabase project.
The portal can be published before a worker is connected; it clearly displays
missing worker heartbeats and leaves jobs queued.

## Python worker on an independent host

The worker is a continuous process with no public HTTP endpoint. Use a Docker
background worker or a VM. Start with at least **2 GiB RAM** and test all selected
modalities against the host's actual limits before treating it as production.

On a Docker host, from the repository root:

```sh
# Set these privately using the host's secret/environment settings.
export OPSFUSION_SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
# Set OPSFUSION_SUPABASE_SECRET_KEY privately; do not commit or paste its value.
docker compose -f compose.cloud-worker.yaml up -d --build
```

For Render, choose a **Background Worker** from this repository and Dockerfile
`deploy/Dockerfile.worker`. Configure the two variables above in Render's private
environment settings. Do not choose the existing web/API Dockerfile for this
worker. Confirm the compute price before creating a paid instance. Render's free
web service is not an always-on background-worker plan. No paid resource is
created by the code or setup instructions.

To exercise the cloud adapter locally instead, install the existing CPU PyTorch,
vision, sensor and audio requirements, plus `requirements-cloud.txt`; set the
same two variables privately and run:

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.cloud_worker
```

That local option stops when the computer is off. Netlify and Supabase alone do
not execute this Python perception pipeline. An independently hosted processor
must be provisioned and verified to satisfy the PC-off requirement.

## Input and output limits

- Video: MP4/AVI/MOV/MKV, at most 50 MiB; inspect up to 60 seconds at 1–10 FPS.
  Supply an actual recording start with timezone and a normalized polygon.
- Sensor/log CSV: at most 5 MiB each; the existing documented schemas apply.
- Audio: mono 16-kHz WAV, at most 20 MiB each, with capture and baseline clocks.
- Sensor/audio captures require separate known-normal training and calibration
  files plus explicit normal-period confirmation. No pickled model uploads.
- At most 150 MiB of inputs per job; results at most 50 MiB per object and
  200 MiB total. Larger runs fail clearly rather than silently truncating.
- A failed/abandoned draft may retain partial private uploads. An interrupted
  worker may leave attempt artifacts. There is no automatic retention/deletion
  policy in this version; review storage usage and define retention explicitly.

Input names are canonicalized by slot. Inputs become immutable when queued.
Worker leases expire after 120 seconds and renew while processing. A crashed
worker can be reclaimed, up to three claims; stale attempts cannot commit results.
Application exceptions mark a job failed. They do not fabricate a successful run.
Reviews append actor/time/status/note records; report snapshots remain unchanged.

The report retains original evidence provenance. Use the portal's evidence
buttons or source-evidence ZIP for the corresponding private cloud objects;
local `file://` provenance references are not public links.

## Acceptance check after hosting

1. Sign in with an enabled member and confirm the correct role.
2. Confirm **Processing worker connected** appears using its real heartbeat.
3. Submit a short known controller CSV, then a controlled video with known entry
   times. Inspect the actual evidence and compare report timing to the source.
4. Download both ZIPs and incident JSON; confirm private downloads work after
   sign-in and fail without authorization.
5. Save a review, reload, and verify the actor, note and history remain saved.
6. Restart the worker during a test job and verify reclaim/fencing behavior on
   the actual host. Stop it and confirm the portal no longer reports it online.
7. Check quota, storage, logs and memory. Shut down the original PC and repeat
   a small job using the hosted worker.

Until these checks run on the selected hosts, a passing local suite is not proof
of a complete live deployment or real-world detection accuracy.

## Database checks

Run `supabase/tests/cloud_access.sql` using the Supabase SQL editor when no jobs
are active. It checks member roles, column permissions, private storage,
append-only reviews and expired/reclaimed worker fencing. All test fixtures roll
back; it does not create Auth users or retain dummy incidents.

The security advisor reports a pre-existing Auth warning: leaked-password
protection is disabled. Review the available setting and plan requirements:
https://supabase.com/docs/guides/auth/password-security#password-strength-and-leaked-password-protection
The performance advisor may report unused indexes on a new empty installation:
https://supabase.com/docs/guides/database/database-linter?lint=0005_unused_index
Do not remove queue/read indexes solely because the new site has no traffic yet.
