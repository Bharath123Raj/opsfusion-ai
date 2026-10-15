# Deployment preparation validation

The user deferred the controlled real recording check (Step14). It remains
pending. Step15 validates software/deployment mechanics with disposable accounts
and synthetic evidence; it does not verify the user's Windows run or field accuracy.

## Software checks

The full 407-test regression run passed with zero failures, errors or skips.
It includes 25 new deployment tests. A final 25-test targeted follow-up covers
the completed access/backup/upgrade changes, including prior cookie-session
revocation, Windows-style restored evidence references, and upgrade blocking
while the supervised service holds its lock. The runs are separately recorded in
[software validation](../reports/step15_software_validation.json).

New cases cover HTTPS/HttpOnly cookie flags; anonymous media verification;
cookie-versus-bearer API separation; wrong credentials without secret echo;
CSRF/Origin/duplicate/oversized/Unicode form rejection; logout/revocation;
fail-closed access configuration; unchanged local access mode; guarded offline
backup; SQLite/account/model/history/evidence preservation; restored session
removal; no source-session revocation by backup; running-service/active-input
guards; no overwrite of existing restore targets; hash mismatch, unsafe members
and symlink rejection; explicit retention preview/apply; evidence path allowlists;
explicit data root; supervision configuration; and Step13-to-Step15 upgrade.

## Actual HTTPS gateway and browser check

[Browser validation](../reports/step15_deployment_browser_validation.json) used
the actual Caddy 2.11.7 binary, downloaded from its official release with the
published asset SHA256 checked. It launched the real API and Streamlit through
the supervisor and a local HTTPS gateway. Its internal fixture CA remained in
a temporary directory and was not installed into the system trust store. Only
the validation clients ignored this fixture certificate; production code has
no TLS-verification bypass.

The browser signed in once through the gateway. Anonymous requests to an actual
snapshot URL and JSON download URL were denied, while the authenticated browser
could retrieve them. An anonymous WebSocket upgrade was denied. The Secure,
HttpOnly account cookie authenticated the dashboard; viewer review controls
were disabled; revocation immediately blocked fresh media requests.

The harness terminated its own API child. The supervisor restarted both API and
dashboard, and the saved review remained. It stopped the service, made a guarded
offline backup, restored into a new data directory, restarted there and logged
in with the preserved account. The original first report and review persisted,
old account sessions were removed, and the original file reference displayed
the restored snapshot through the bounded origin mapping. Zero browser
JavaScript errors or final Streamlit exception panels were observed. The fixture
image is visibly synthetic; it tests no person detector or physical device.

## Configuration checks and limits

Both Compose YAML files were parsed and checked for private backend ports and a
named persistent data volume. The Caddy preview configuration was validated and
the HTTPS configuration adapted successfully. See
[configuration validation](../reports/step15_configuration_validation.json).

The environment had no Docker engine, so the Docker image build, Compose
container startup, volume ownership on the chosen host, resource limits under
real load and public certificate issuance remain unverified. No provider,
domain, public deployment, paid account or external notification was provisioned.
Step16 performs actual hosting and Step17 acceptance on that environment.

Other limits: single-site shared read access; no tenant isolation/SSO/MFA;
existing per-username failed-login limits rather than a production abuse-defense
system; trusted local OS/admin/model files; no physical capture bridge to a
remote laptop webcam; no independent security audit or industrial safety claim.
Backups are private plaintext archives containing password hashes and evidence;
SHA256 integrity checks do not authenticate an archive's author. Offline
attestation still requires standalone processes to be stopped. User data is
not automatically purged. Causes stay unconfirmed; no machine action executes.
