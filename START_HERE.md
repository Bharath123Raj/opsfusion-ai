# Step16 — Free cloud deployment

You chose **free tier only** and **online when your computer is off**. The next
action is to create an Oracle Cloud Free Tier account and an Always Free ARM VM.
This release contains the full app plus deployment and migration tooling; it
does not mean a server has already been deployed.

Follow [the Windows/Ubuntu cloud guide](docs/FREE_CLOUD_DEPLOYMENT.md). It covers
Oracle signup, VM settings, a free DuckDNS hostname, SSH upload, Docker build,
existing-account migration, HTTPS startup and backups.

Target: Ubuntu 24.04 ARM64, VM.Standard.A1.Flex, 2 OCPUs, 12 GB RAM, 100 GB boot,
subject to current free eligibility and your remaining quota. Oracle requires
card verification. Free capacity may be unavailable and idle VMs may be reclaimed;
this route cannot guarantee uninterrupted availability. Do not choose paid or
trial-only resources if the free shape is unavailable.

## Existing Windows installation

Keep your existing upgraded Step10 folder, bharath account, password and saved
data. The cloud guide makes an offline backup and restores it into a NEW cloud
site. Do not bootstrap your existing Windows account again. Windows and cloud
copies do not synchronize automatically.

Update local code to 0.16.0 before making the migration backup. This includes a
fix that closes copied SQLite connections before computing archive checksums.
Close streams/watches,
stop workers and the old dashboard/API, then open PowerShell in the NEW Step16
folder containing dashboard.py:

```powershell
conda run -n opsfusion312 python -m opsfusion.upgrade --project "$env:USERPROFILE\Downloads\OpsFusion-AI-Step10\OpsFusion-AI-Step10"
```

This accepts the existing 0.15.0 code and preserves private data. Use a new backup
made after this update; keep older backups intact. Cloud helper
commands belong on the VM, not in your Windows Conda terminal.

## Remaining work

- Step14: your controlled real recording test remains deferred.
- Step15: deployment preparation and recorded software checks are included.
- Step16: free-host files prepared; actual VM, Docker build and public HTTPS
  remain pending your provider account and server.
- Step17: actual-host acceptance after public startup.

No hardware is integrated. The VM can process a previously uploaded recording
with your PC off; fresh webcam input needs an online acquisition device. Email
remains deferred. See docs/STEP16_VALIDATION.md for the verification scope.
