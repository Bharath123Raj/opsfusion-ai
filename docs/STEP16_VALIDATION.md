# Step16 verification scope

The final full run passed **417 tests in 140.478 seconds**, with no failures,
errors or skips. The ten new cloud tests also passed in a targeted run.

The software results are recorded in
[step16_software_validation.json](../reports/step16_software_validation.json).
Configuration and native ARM wheel availability are recorded in
[step16_configuration_validation.json](../reports/step16_configuration_validation.json).
The test environment is Linux AMD64 with Python 3.12, using the existing pinned
Step15 app dependencies. These checks do not establish Oracle ARM runtime
compatibility or a public deployment.

## Cloud and migration checks

The new cloud tests exercise hostname validation, private exclusive configuration
creation, symlink rejection, environment override protection, stable Compose
selection, offline restore checks and archive streaming without a host mount.
A real Python subprocess receives backup bytes on stdin and restores an account;
the original password still works, copied sessions fail, and a second import
cannot overwrite the restored site. Startup checks account initialization before
calling Compose up. Step15-to-Step16 upgrade preserves private data, excludes
source environment files and supports an already-current no-op.

YAML inspection confirms the `opsfusion-cloud` project, `/data/site` data root,
durable app/Caddy volumes, private backend ports, published gateway 80/443 and
restart policies. The HTTPS Caddyfile is byte-identical to the configuration
used for Step15's native browser validation; that earlier browser result is
retained in reports/step15_deployment_browser_validation.json. It was not a
Docker, Oracle or public-CA test and is not represented as one here.

## Backup checksum correction

The initial full run exposed a hash mismatch when restoring one copied SQLite
database. SQLite connection context managers commit transactions but leave the
connection open. A later garbage-collection-triggered close could checkpoint a
WAL and change main-database bytes after the backup manifest hash was computed.
Backup snapshot, session-removal and restore-integrity connections are now
explicitly closed before the next stage.

A regression test deliberately runs garbage collection after hashing and before
ZIP serialization. It reproduces the integrity failure against the previous
Step15 implementation and passes with the corrected code. The cloud guide
requires updating Windows code before generating a NEW migration backup. No
hash check was relaxed, and populated restore targets remain protected.

## Checks still required on the actual host

- Oracle account and free VM eligibility/capacity.
- ARM64 Docker build, pinned dependency imports and named-volume permissions.
- Real DNS, publicly trusted TLS and authenticated browser/media access.
- Migration of the user's actual private backup and evidence previews.
- End-to-end input processing, permissions, VM reboot and offline restore.

No provider account, SSH session, VM, public hostname or public certificate was
created by these software checks. No private user data was transferred. There
is no measured cloud throughput, high-availability guarantee or new hardware
accuracy result. Step14 remains deferred, Step16 actual hosting is pending and
Step17 is acceptance on the chosen host. Email remains deferred.
