# Step13 validation scope

This package adds explicit live camera/HTTP correlation without changing the
existing source report contracts. Evidence is recorded in
[software validation](../reports/step13_software_validation.json) and
[browser validation](../reports/step13_linked_browser_validation.json).

## Software regression

The complete 381-test regression run passed with zero failures, errors or skips.
It includes 36 new correlation tests. A final 37-test targeted run adds one
wrong-installation demo preflight check; there are 382 distinct tests in the
delivered tree. The machine-readable report records both runs separately.

New cases cover selected-source and asset isolation; no implicit/backfilled
links; horizon boundaries and nontransitive pairing; reverse cross-source
arrival; stale/future declarations; known entry versus initial presence;
frozen first and revised current reports; review reopening; camera/sensor gap
boundaries; source closure/stop; watch capacity without stopping sources;
concurrent retries; atomic rollback for both camera and HTTP ingestion;
persisted links/reports; supporting three-reading proofs/baseline provenance;
viewer/operator/admin access and revoked tokens; strict authenticated API;
installation identity, sanitized login errors and in-place upgrades.

## Actual local HTTP and browser workflow

`tools/validate_watch_browser.py` launched real Uvicorn and Streamlit subprocesses
with a disposable site, registered a synthetic baseline, and executed the paced
CLI sender. The sender decoded 36 generated frames over approximately 18 seconds
through the real frame Processor/ZoneTracker/snapshot/history path and sent
18 readings plus 2 authored controller records over actual loopback HTTP.

Observed: 2 zone entries, 3 machine observations, 5 linked evidence events,
2 joint reports, and 7 total source/joint reports with 7 local notices. All joint
reports remained synthetic with unconfirmed causes. The watch closed when its
source finished. Browser checks verified live counts, joint report selection,
camera snapshot and three-reading raw input previews, evidence JSON download,
saved review after reload, viewer read-only controls, and zero JavaScript or
Streamlit exception panels. No external notifications were sent.

The visual fixture contains a marked green rectangle and deliberately uses a
test-only color detector. It is not YOLO person inference, real camera footage
or hardware validation. Real webcam/RTSP workers still use their existing YOLO
path; earlier versions' proof files retain their original scope/version.

## Not established

No real industrial sensor, controller, authorization system or independent
person-entry labels were evaluated. No physical clock alignment, false-alarm
rate, fault recall, root-cause accuracy, safe machine action or production load
limit is established. The user's own recording test remains pending. Email is
optional and deferred; external SMTP provider acceptance remains unverified.
Only one selected camera and one HTTP session are linked per watch. Live audio,
multiple-camera correlation, hosted private media and hardware protocol bridges
remain future work. Joint/source report counts are review categories, not counts
of independent physical incidents.
