Step13 update: opt-in active camera/HTTP source links now produce bounded joint
cooccurrence reports. See [LINKED_SOURCES.md](LINKED_SOURCES.md) for current
linking, clocks, gap boundaries and first/current evidence rules. Earlier
session-scoped fusion below remains available and keeps its existing reports.

# Live HTTP sensor/controller contract — v0.13.0

This service accepts actual HTTP requests on loopback, scores registered sensor
baselines causally and persists inputs/observations/incidents/local notices in shared
runs/history.sqlite3. The included CLI sender is explicitly generated test data.
The acquisition client is responsible for measuring correct physical units and clocks.
No hardware driver, MQTT broker or OPC UA client is bundled.

## Registered baselines and roles

An admin creates a baseline locally from known-normal training/validation CSVs:

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.stream register-baseline --username bharath --train .\data\normal-train.csv --validation .\data\normal-validation.csv
```

The existing [sensor contract](SENSOR_CONTRACT.md) governs units, minimum rows,
chronological splits, feature order, sklearn version and synthetic provenance.
Each asset needs 120 training/80 validation rows; inference must follow validation.
Known-normal status is an operator assumption, not a verified fact. Do not put g,
RPM, pressure or inferred vibration into the mm/s field.

Models are immutable registered IDs under runs/stream-baselines, with SHA256,
asset metadata and registering account in SQLite. No HTTP model path/upload/load
endpoint exists. Admin/operator users may select only these server-registered IDs;
only admins can train/register models. Viewers read status/evidence; ingestion and
close mutations are denied. Bearer sessions use the existing accounts database.
Role/expiry/revocation is rechecked on actions. Local OS/file administrators remain
trusted; joblib models are Python objects, never accept an untrusted replacement.

## Routes

| Method / path | Purpose |
|---|---|
| POST /auth/login | Existing username/password → bearer; no-store; client retains it only in memory |
| GET /v1/streams/contract | Units, bounds and policy |
| GET /v1/streams/baselines | Registered IDs and asset metadata |
| POST /v1/streams | Open an asset session |
| GET /v1/streams | Latest 100 stream statuses |
| GET /v1/streams/{id} | State, counts and last receipt age |
| POST /v1/streams/{id}/readings | One raw sensor measurement, atomically scored/saved |
| POST /v1/streams/{id}/logs | One declared controller record, mapped or retained unmapped |
| POST /v1/streams/{id}/close | Idempotently close ingestion; does not resolve faults |
| GET /v1/streams/{id}/export | Transactional saved-input/event evidence snapshot |
| POST /auth/logout | Revoke bearer session |

Open request (replace registered ID):

```json
{"baseline_id":"0123456789abcdef0123456789abcdef","asset_id":"warehouse-zone-01","source_id":"local-device-client","synthetic":false}
```

Sensor request (acquisition time must be current when actually sending):

```json
{"message_id":"device-000001","timestamp":"2026-10-14T08:00:00+00:00","asset_id":"warehouse-zone-01","vibration_rms_mm_s":2.15,"temperature_c":55.6,"synthetic":false}
```

Controller request:

```json
{"message_id":"controller-000001","timestamp":"2026-10-14T08:00:01+00:00","asset_id":"warehouse-zone-01","level":"WARN","code":"MOTOR_OVERLOAD","message":"Reported controller warning","synthetic":false}
```

Use Authorization: Bearer TOKEN. Tokens/passwords are not query parameters or saved
in stream evidence. The raw controller message is saved as supplied; acquisition
clients must select an appropriate non-secret operational message. The original
stateless event-review routes still coexist and never read caller evidence paths.

## Time, idempotency and scoring

Acquisition timestamp is a client declaration, normalized to UTC. The server
stores a separate received_at clock. It does not establish hardware synchronization
or repair clock drift. New inputs must be no more than 30 seconds old / 5 seconds
future. Arrival timestamps are nondecreasing across sensor/log inputs and strictly
increasing within each kind. Sensor+log may legitimately share one timestamp.
Late/repeated-kind timestamps are rejected, not silently rewritten; no buffering,
watermarks or historical backfill route is included. A future actual controller
with simultaneous multiple same-kind records will require a batch contract.

message_id is unique within a stream across both kinds. Retrying identical canonical
content returns the original acknowledgement with replayed=true, even after closure
or the receipt window. It does not increment counts/streak or repeat alerts. Changed
content under the same ID returns 409. Source/asset/synthetic declarations are fixed
at creation; synthetic training propagates to all derived sensor/controller evidence.

Scoring uses the existing per-asset Isolation Forest and robust residual rules.
Three consecutive sensor candidates confirm an episode entry; a normal reading or
gap >2× learned cadence resets persistence. Logs cannot confirm the sensor streak.
Continuous anomalous readings stay in raw evidence but do not emit repeated sensor
events. Restarting the API retains saved state and idempotency acknowledgements;
models stay fixed. A changed registered model file on a fresh load is rejected by
hash; a process's cached immutable model stays frozen until its cache is reloaded.

Input/result/streak/event/report/notice updates share one SQLite transaction. A
failure rolls back the accepted input and confirmation. Concurrent retries are
serialized. The response includes scores, candidate/alert, event_id if emitted,
synthetic provenance, gap-reset status and causal confirmation input IDs.

## Controller mapping and fusion

WARN/ERROR/CRITICAL TEMP_WARNING, VIBRATION_WARNING, MOTOR_OVERLOAD produce
motor_warning observations. UNEXPECTED_SHUTDOWN/PROTECTIVE_SHUTDOWN produce
shutdown observations, matching the earlier CSV adapter. Other codes/levels are
saved as unmapped records with no invented event. Free text never creates a diagnosis.

Within one HTTP asset stream, sensor/log observations enter the existing quiet-gap
fusion engine (90s horizon, 20s gap). Cross-modality support can create possible
degradation/shutdown-following-warning reports; all causes stay unconfirmed.
First reports remain frozen; later evidence can update current reports and reopen
operator-closed ones. A gap resets future association, not past facts or evidence
that a machine recovered. Short normal periods may still fall within one fusion
episode; this follows the existing documented segmentation heuristic.

Unlinked camera/HTTP sessions keep separate reports. Explicit Step13 watches add bounded joint cooccurrence reports; see LINKED_SOURCES.md. Continuing
sensor faults emit transition events only; a distant later controller warning may
lack recent sensor-event support even while raw readings remain anomalous.
No machine-control action, auto shutdown or automatic maintenance task executes.

## Limits and lifecycle

At most 20 open HTTP streams, one per asset; 10,000 total input records and 200 derived
events per stream; the existing 2 MiB HTTP request body cap remains. This is one-input
per request, not a high-rate industrial broker. Full stream export is bounded by
those capacities. There is no fleet-wide retention, per-second rate quota or measured
production throughput claim. Close and rotate sessions before reaching capacities.

The dashboard auto-refreshes stream status/last 100 inputs every two seconds.
Input status stale means no accepted input for >10 seconds, not a fault diagnosis
or recovery. No automatic closure occurs when a client/browser/API disappears;
reconnect to the same ID or close it in the authenticated dashboard. An interrupted
test sender tries to close; a lost open-acknowledgement can leave an orphan open
session requiring explicit close. Only record POST retries are safely idempotent.

Keep API and dashboard in the same installation folder and bound to 127.0.0.1.
The supplied test client rejects remote/user-info URLs, disables proxy inheritance
and redirects, and keeps credentials out of process arguments/reports. A real device
can use a local acquisition bridge with this contract; direct LAN/cloud device
access needs authentication/TLS/network deployment work beyond this milestone.

## Primary references

- [FastAPI authentication dependencies](https://fastapi.tiangolo.com/tutorial/security/get-current-user/)
- [Python sqlite3 transactions](https://docs.python.org/3.12/library/sqlite3.html#transaction-control)
- [scikit-learn IsolationForest](https://scikit-learn.org/1.5/modules/generated/sklearn.ensemble.IsolationForest.html)

The validation report documents generated inputs and actual local HTTP behavior.
It does not establish real machinery/camera accuracy or clock calibration.
