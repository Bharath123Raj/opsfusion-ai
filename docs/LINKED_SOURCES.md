# Explicit camera/HTTP correlation

Admins/operators can link one active webcam/RTSP/file-replay session and one open
HTTP stream for the same exact asset ID. Viewers can inspect/export. There is
one active watch per selected source and at most 10 active watches. A link is an
operator declaration of mapping, not verification of physical installation.

Existing source reports retain their IDs, reviews, first/current snapshots and
notices. A watch adds joint reports; it does not retrospectively merge source
reports. Several review categories may concern the same physical activity.

## Time and arrival rules

Only newly committed observations after linking are eligible. Existing events
are not backfilled. Eligible declarations must be at or after the latest link
or gap boundary, <=30s old and <=5s future at receipt, with confidence >=0.6.
Time exclusions increment excluded_count without deleting source evidence.
Upstream HTTP admission rules remain independently enforced.

Camera time is host frame receipt time; HTTP acquisition times are client
declarations. Cross-source arrival order may differ within the admission range.
This is bounded arrival handling, not clock correction, an event-time watermark
or arbitrary historical reconciliation. First snapshots depend on observations
available in the atomic arrival transaction. Later evidence changes only current
reports. Evidence is sorted by declared UTC time/event ID; detected_at records
the first joint processing time and remains fixed during revisions.

## Joint rule

Each zone_entry or zone_presence observation is a separate fixed time anchor.
A zone_machine_cooccurrence report requires a selected HTTP sensor_anomaly,
motor_warning or shutdown observation within ±horizon of that anchor. Default
horizon is 10s; range is 1–30s. Warning chains cannot extend this window. Only
emitted adapter observations participate; raw continuous anomalous readings do
not extend machine evidence indefinitely.

Joint reports preserve initial presence versus known entry and mark synthetic
if any supporting observation is synthetic. The hypothesis remains unconfirmed.
Camera presence does not establish a machine fault, unauthorized access, danger
or causation. Shutdown is a reported controller state. No machine action executes.

Camera capture boundaries and sensor cadence gaps create a new watch generation;
observations across the boundary cannot pair. Recent declarations from before
the boundary are also excluded. Existing reports remain saved. Camera
stop/finish/stale recovery or HTTP closure closes the watch atomically. Closing
a watch itself leaves its sources running. Source replacement needs a new link.

## Persistence and evidence

Source events/inputs, joint reports and local notices commit together in the
same BEGIN IMMEDIATE transaction. Failure rolls back related changes. Identical
source retries do not duplicate reports/notices. New evidence after a resolved
or false_alarm review reopens the report using the existing history audit rules.

A watch stores at most 1,000 eligible events. Overflow closes only the watch
with watch_capacity_reached; source ingestion continues. Pairing and exports are
bounded by that cap. Lists show the latest 100, prioritizing active watches.
There is no automatic retention/supervisor; crashed cameras need explicit stale
recovery through the existing controls.

Exports include source IDs, generations/counters, events, first/current reports,
watch/review audit, baseline ID/hash/asset metadata and supporting stored inputs.
Sensor proof includes the three confirmation readings available at that time.
No password, bearer or SMTP configuration is added. Controller text is exported
as supplied; acquisition clients should avoid secret operational text. Images
remain local references previewed through the existing evidence path allowlist;
JSON is not a media backup. Existing local media-access limitations still apply.

## API

All routes require bearer login. Request bodies remain limited to 2 MiB.
Link bodies have exactly camera_session, http_stream and optional finite
horizon_seconds; source IDs are 32-character lowercase hex. There is no HTTP
camera-event injection, arbitrary file-path or model-upload operation.

| Route | Operation |
|---|---|
| GET /v1/watches/installation | Authenticated opaque installation ID for demo profile matching; no paths |
| GET /v1/watches | Recent watch metadata |
| POST /v1/watches | Link selected active sources |
| GET /v1/watches/{id} | Watch metadata |
| POST /v1/watches/{id}/close | Idempotently close watch; sources continue |
| GET /v1/watches/{id}/export | Transactional evidence/report/audit snapshot |

Missing/expired authentication is 401, forbidden role 403, invalid fields/state
422. Real camera events still come from the local capture worker. Keep loopback
binding; this is not a secure LAN device gateway or hosted tenant system.

## Marked integration fixture

Run `python -m opsfusion.watch demo --username YOUR_OPERATOR` in the active
installation while its API runs. The demo checks an opaque installation ID,
reuses a registered synthetic baseline, generates/decodes an 18-second MJPEG AVI
and uses a test-only green-rectangle detector. Frame observations go through
Processor/ZoneTracker/snapshot/history. Readings/logs use actual loopback HTTP
and the registered model. Sources close and the bearer logs out in finally
where possible. Requests are not automatically retried; if a lost response
leaves an unacknowledged stream, close it in the dashboard before retrying.

Expected: 36 decoded frames, 18 readings, 2 controller records, 2 zone entries,
3 machine observations, 2 joint reports and 7 total new source/joint reports
with 7 local first-alert notices. Synthetic provenance remains explicit. This
checks software integration; it measures no hardware, person-detector or
industrial fault accuracy. Real webcam/RTSP workers retain YOLO inference.
