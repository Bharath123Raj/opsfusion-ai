# OpsFusion AI

OpsFusion combines observations from video, sensor measurements, machine audio
and controller logs into evidence-backed incident reports for human review.
The local application includes a Streamlit dashboard, FastAPI API and Python
processing adapters. The cloud interface uses React on Netlify with Supabase
Auth, Postgres and private file storage, plus a separate Python worker.

**Status:** local version **0.16.0** plus the implemented cloud portal and worker.
The Supabase schema is installed and access-tested. The web interface is
[published on Netlify](https://opsfusion-ai-bharath.netlify.app/) using a Free
account. Google redirect configuration, an independent processing host and
end-to-end acceptance are still required.
See [cloud deployment](docs/CLOUD_DEPLOYMENT.md) for setup and limits.

## What works today

- **Video:** YOLOv8n person detection, configured polygon zones, tracking,
  confirmed entries and initial-presence observations, snapshots and annotated
  recordings. Initial presence is distinguished from a witnessed entry.
- **Sensors:** per-asset anomaly baselines, chronological training/calibration,
  persistence rules and confirmed anomaly episodes.
- **Audio:** acoustic anomaly scoring against trained normal baselines, with
  persistence checks and episode evidence.
- **Controller logs:** explicit mappings from structured log codes to operational
  observations.
- **Fusion and review:** asset/time alignment, incident grouping, frozen first
  reports, later revisions, evidence, review decisions and audit history.
- **Local live monitoring:** webcam/RTSP capture, authenticated HTTP
  sensor/controller streams and explicit camera/HTTP correlation watches.
- **Local accounts:** admin, operator and viewer roles; authenticated API access.
- **Exports:** incident JSON, review bundles, score tables and source evidence.
- **Operations:** local service supervision, persistent SQLite history and guarded
  offline backup/restore. Email and webhook delivery remain optional.

Reports support investigation. Proposed causes remain unconfirmed, and the
application does not issue machine-control commands.

## Cloud architecture

| Component | Responsibility | Status |
| --- | --- | --- |
| Netlify | React login, uploads, progress, private downloads and incident review | Published on a Free Netlify account; GitHub `main` linked |
| Supabase | Hosted Auth, jobs, reports, reviews, worker leases and private media | Schema installed; role, storage and stale-worker checks passed |
| Python worker | Validate recordings, run adapters/fusion, save results | Implemented and tested locally; independent host pending |

Cloud jobs process **uploaded recordings**. Existing local webcam/RTSP and HTTP
monitoring features remain local; this change does not migrate their live feeds.
Local SQLite accounts/history are separate from Supabase accounts and data.
This is one shared site with admin/operator/viewer roles, not tenant isolation.

The selected architecture replaces the earlier Oracle route. Existing VM/Caddy
files remain an alternative. This deployment is restricted to **free resources only**. No paid processing
service has been created. A suitable free, continuously running Python host
has not been established. Netlify and Supabase keep the portal/data online;
processing continues with the PC off only when its worker has an independent
host. Fresh device input also requires an online acquisition device.

For the web interface, use Node 24:

```sh
cd web
npm ci
npm test
npm run build
```

Set the two public variables shown in `web/.env.example` for local development.
Netlify's configuration includes this installation's public project URL/key.
Backend secrets are accepted only by the Python worker environment.
See [deployment and acceptance](docs/CLOUD_DEPLOYMENT.md) before publishing.

## Run the existing application locally

Use **Python 3.12**. The commands below use the existing Conda environment
`opsfusion312`; run them from the repository folder containing `dashboard.py`.
If you already have that environment, reuse it.

### Install dependencies

```powershell
conda run -n opsfusion312 python -m pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu
conda run -n opsfusion312 python -m pip install -r requirements-app.txt -r requirements-vision.txt -r requirements-sensor.txt -r requirements-audio.txt
```

For a **fresh data installation only**, create an initial administrator:

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.accounts bootstrap --username admin
```

Choose your own password of at least 12 characters. Password input is hidden.
If upgrading an existing installation, keep its accounts and data in place;
do not bootstrap it again. A fresh Git clone does not contain your earlier
Windows account, runs, models or recordings.

### Start API and dashboard together

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.service
```

- Dashboard: http://127.0.0.1:8504
- API documentation: http://127.0.0.1:8000/docs

Sign into the dashboard with an account from that installation. API operations
require a bearer token obtained through `/auth/login`. Keep the local backend
bound to loopback; public access requires the deployment's authentication and
storage design.

### Analyse recordings

Choose **Real recordings** in the dashboard. Supply the appropriate files and
metadata, then run the analysis:

| Input | Required context |
| --- | --- |
| MP4/AVI/MOV/MKV video | Recording start, asset/camera IDs and actual zone polygon |
| Sensor CSV | Timestamped measurements and normal training/calibration data or a trusted baseline |
| Mono 16-kHz WAV | Recording start, asset ID and normal training/calibration audio or a trusted baseline |
| Controller log CSV | Timestamps, asset IDs and supported controller-code mappings |

Sources must refer to the same real asset and aligned times for meaningful
multimodal correlation. Generated example data remains marked synthetic.
Only runs explicitly saved to incident history become durable history entries.
See [real-data integration](docs/REAL_DATA_INTEGRATION.md) for input contracts.

## Validation and limits

The recorded Step16 Linux/Python 3.12 run passed **417 software tests**, including
backup/restore and deployment-tooling checks. This is a recorded development
result, not proof of public-cloud deployment or real-world detection accuracy.

The cloud integration check passed **423 Python tests**, **6 browser-input tests**,
and **6 browser workflows using mocked cloud responses**. The actual Supabase
role/storage/lease SQL checks also passed and rolled back their fixtures.
See [cloud validation scope](reports/cloud_foundation_validation.json).
These checks do not verify the pending hosts or a real Google login round trip.

To run the suite in your installed environment:

```powershell
conda run -n opsfusion312 python -m unittest discover -s tests -v
```

See [Step16 validation](docs/STEP16_VALIDATION.md) and
[the recorded software results](reports/step16_software_validation.json).

- The controlled own-recording accuracy test remains pending.
- Sensor/audio examples include synthetic data; no physical sensor/controller
  hardware has been integrated or calibrated.
- Live audio correlation and direct MQTT/OPC UA device bridges are not implemented.
- Local role permissions do not provide isolation between independent customers.
- Cloud authentication, storage policies and worker execution are implemented.
  Hosting, restart/recovery and end-to-end acceptance still need actual-host
  validation. No public deployment or physical accuracy claim follows from tests.
- Optional email delivery is deferred in the current walkthrough.

## Data and credentials

Keep environment secrets, Supabase service-role credentials, account databases,
private recordings, generated evidence, trained models and backups out of Git.
The repository includes synthetic fixtures and development reports for testing.

Backups contain private evidence and password hashes. The local backup tool does
not encrypt them. Keep a private copy outside the machine being backed up.

## Documentation

- [Netlify/Supabase cloud deployment](docs/CLOUD_DEPLOYMENT.md)

- [Raw video/sensor/audio/log integration](docs/REAL_DATA_INTEGRATION.md)
- [Observation event contract](docs/EVENT_CONTRACT.md)
- [Accounts and notifications](docs/AUTH_NOTIFICATIONS.md)
- [Live monitoring and saved history](docs/LIVE_HISTORY.md)
- [HTTP sensor/controller streams](docs/HTTP_STREAMS.md)
- [Linked camera/HTTP sources](docs/LINKED_SOURCES.md)
- [Existing VM/gateway and backup design](docs/DEPLOYMENT.md)
- [Existing Step16 validation scope](docs/STEP16_VALIDATION.md)

The historical step guides describe the implementation milestones. Their Oracle
walkthrough is an alternative deployment route; the selected Netlify/Supabase
deployment status is described above.
