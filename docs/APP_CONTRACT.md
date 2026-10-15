Step15 update: the optional authenticated gateway now gates Streamlit/media
traffic when the backend remains private. Persistent data, supervision and
backup/restore are documented in [DEPLOYMENT.md](DEPLOYMENT.md). Direct local
8504 access retains its earlier limitations; all accounts still share one site.

Step13 update: opt-in active camera/HTTP source links now produce bounded joint
cooccurrence reports. See [LINKED_SOURCES.md](LINKED_SOURCES.md) for current
linking, clocks, gap boundaries and first/current evidence rules. Earlier
session-scoped fusion below remains available and keeps its existing reports.

Step12 adds persisted raw HTTP sensor/controller routes alongside the existing
stateless review API. See [HTTP_STREAMS.md](HTTP_STREAMS.md) for that separate contract.

# Local app/API contract — Step 8

Historical Step8 contract. Step10 adds bearer authentication for /v1 routes,
dashboard roles, authenticated review actors and a separate notification outbox.
Existing local model-file loading is now admin-only. See the current
[accounts/notifications contract](AUTH_NOTIFICATIONS.md); event limits and
stateless replay semantics below remain in force.

Both surfaces call `opsfusion.app_service`; the dashboard does not call the API over HTTP. Neither accepts serialized model uploads or executes shell commands, source instructions or machine actions. The local raw-data dashboard can load a model file explicitly selected and attested as trusted by the operator; sensor.joblib uses Python deserialization. The HTTP API does not expose raw media processing or local model paths.

## Input

Dashboard v0.8.1: raw recordings/CSV files, event JSONL, or explicitly selected synthetic demos + optional independent incident-label JSON. Raw inputs follow [the real-data integration contract](REAL_DATA_INTEGRATION.md). API: a JSON object with `events`, optional `engine` (`episode` default), `window_seconds` (90 default), and `quiet_gap_seconds` (20 default). These numerical settings must be finite, positive, nonboolean and at most 3600 seconds. Fixed replay records quiet gap as null because it is inactive; the requested gap is retained separately for comparative review.

Events use exactly the existing event contract. All fields are required; unknown event fields are rejected. Strings are nonempty and bounded: ID/source/asset 256, timestamp 64, modality 32, kind 64, evidence reference 2048, summary 4000 characters. Timezones are required. Confidence is an integer/float in [0,1], not a boolean; synthetic is a strict boolean. Integer confidence values are preserved to match canonical legacy CLI replay identities. Thresholding remains the existing 0.6 source-quality rule.

Request/upload limit: 2 MiB. Review input count: 200 before deduplication; benchmark count: 100 before deduplication. Identical duplicate IDs are deduplicated, conflicting IDs rejected. Label interval lists are bounded to 500 each; the existing evaluation code validates intervals, coverage, uniqueness and matching. Scope and synthetic provenance are required. Predictions outside declared coverage cannot be scored.

No file paths, replay snapshots, LLM settings or serialized models are accepted as executable API input. Imported event evidence references remain metadata; they are not automatically fetched. The raw dashboard renders only evidence artifacts minted by its adapters and contained under its own raw run directory. Downloaded HTML may include user-clicked evidence links under the existing replay report policy.

## Output

`POST /v1/replay`: `opsfusion_app_run_v1` envelope with canonical deduplicated events, existing replay output, first-scope explanations, provenance counts, requested review settings, offline explanation mode, and zero live model calls.

`POST /v1/explain`: replay input plus `incident_id` and `scope` (`first` default; `final` also accepted). The service rebuilds the incident from the supplied events, rather than trusting client-written fact snapshots. A missing incident in the selected scope returns 404.

`POST /v1/benchmark`: replay settings/events plus `labels`. The engine field is accepted for a common request shape; this endpoint always compares both engines and always runs the nine fixed-engine ablations. Reports include full match/missed/ignored-category detail. No early matching, no threshold sweep or automatic configuration selection. Undefined metrics remain JSON null.

`POST /v1/export`: application/zip with events.jsonl, replay.json, incidents.json (final report envelope), explanations.json (first-scope report list), HTML reports and run_manifest.json. Media and credentials are excluded. Dashboard exports additionally include currently computed benchmark results. Selected final-scope explanations can be downloaded separately; the main ZIP always names its first explanation scope explicitly.

## Operation

API has no persistence and no authentication. Launch on 127.0.0.1. Dashboard state is per browser session; no global cached data or shared incident store. UI uploads and computations stay in the local process. Downloads are the durable review outputs. API and dashboard can run independently.

The pinned Streamlit version can reset the active tab to Timeline during widget reruns. Reopening the desired tab shows the retained results; tab selection itself is not persisted in server session state.

Malformed events/settings/labels return 422; oversized HTTP bodies return 413. Pydantic/OpenAPI supplies request schemas. The body limit also checks received chunks when Content-Length is absent. The replay algorithm remains the full-history reference implementation, not a scalable production streaming engine.

First scope freezes event records, not bytes at external media references. Source truth, perception accuracy, cause probability and authorization are not inferred from successful structural validation. Live LLM selection remains an explicit CLI feature with disclosed fallbacks.
