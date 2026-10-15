# Step16 — Free server independent of your PC

Selected route: **Oracle Always Free ARM VM + DuckDNS + protected Caddy HTTPS**.
The complete application and its durable Docker data volume run on the VM.
This package prepares deployment; it has not created or published a server.
The original Windows installation remains intact, and email remains deferred.

Oracle requires card/identity verification. Free capacity may be unavailable,
and idle VMs may be reclaimed. This route has no uninterrupted-availability
guarantee. Use only resources within your console's current Always Free quotas;
do not choose trial-only resources or upgrade to paid service to bypass limits.

## 1. Create the Oracle VM

Open https://www.oracle.com/cloud/free/ and complete signup yourself. Never send
card details, passwords, MFA codes, private SSH keys or cloud tokens in chat.
In your **home region**, choose **Compute → Instances → Create instance**:

| Setting | Choose |
| --- | --- |
| Name | opsfusion |
| OS | Ubuntu 24.04 LTS, ARM64, compatible with Always Free |
| Shape | VM.Standard.A1.Flex |
| CPU / RAM | 2 OCPUs / 12 GB, assuming no other A1 allocation |
| Boot volume | 100 GB, within the remaining free storage quota |
| Network | Public subnet with internet route and public IPv4 |
| SSH key | Generate a pair and save the private key safely on your PC |

Check free eligibility and the estimate in your console before creating. Oracle
currently documents 2 OCPUs/12 GB for A1 and 200 GB combined boot/block storage;
historical tutorials using 4 OCPUs/24 GB are not used here. No paid GPU, database
or load balancer is needed. If A1 capacity is unavailable, try another availability
domain in the same home region or wait; stop rather than selecting a paid shape.

In the subnet security list or attached network security group, allow inbound
TCP **22 from your own public IP/32**, and **80/443 from 0.0.0.0/0**. Preserve
outbound access for downloads/certificates. Keep 8000/8504 private. Do not flush
firewall rules or open all ports; Docker's published ports can bypass UFW rules.

## 2. Set a free hostname

Sign into https://www.duckdns.org/ yourself. Add an available hostname and set
its current IP to the VM's public IPv4, giving `YOUR-NAME.duckdns.org`. The app
does not need the DuckDNS token. Update the DNS record manually if the VM IP
changes. Check in Windows PowerShell:

```powershell
Resolve-DnsName YOUR-NAME.duckdns.org -Type A
```

It must return the VM's public address, not your laptop's private Wi-Fi address.

## 3. Upload Step16 and connect

In **Windows PowerShell**, replace the key filename and YOUR-VM-IP:

```powershell
scp -i "$env:USERPROFILE\Downloads\YOUR-SSH-KEY.key" "$env:USERPROFILE\Downloads\OpsFusion-AI-Step16.zip" ubuntu@YOUR-VM-IP:/home/ubuntu/
ssh -i "$env:USERPROFILE\Downloads\YOUR-SSH-KEY.key" ubuntu@YOUR-VM-IP
```

Verify the first SSH host fingerprint using the VM console information. After
connection, the prompt is Ubuntu. The remaining Linux commands belong on that
VM, not in Conda or your Windows Step10 folder.

## 4. Install Docker on the fresh Ubuntu VM

These commands use Docker's official repository. If Docker is already installed,
use the official conflict/upgrade instructions instead of reinstalling it.

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl unzip python3
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "$VERSION_CODENAME")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker run --rm hello-world
sudo docker compose version
unzip OpsFusion-AI-Step16.zip
cd OpsFusion-AI-Step16
```

The host Python runs the standard-library helper. Python 3.12 and the pinned app
libraries are installed inside Docker. Commands below use sudo; adding your
login to the Docker group is unnecessary. Reserve ports 80/443 for this stack.

## 5. Configure and build

```bash
sudo python3 deploy/cloud.py configure --domain YOUR-NAME.duckdns.org
sudo python3 deploy/cloud.py build
```

Configuration creates a private `.env.cloud` containing only the hostname and
refuses an existing file. Build downloads dependencies and model weights and can
take several minutes. Matching CPU torch/torchvision wheels exist for Python
3.12 ARM64, but the actual ARM image build remains unverified until this runs
on your VM. If it fails, share the final error; do not publish the raw dashboard.

## 6. Restore your existing account and history

This is the recommended path for your existing **bharath** account. In the
Windows dashboard, close HTTP streams/watches and stop camera workers. Stop the
local service and notification worker, if any. First open **Windows PowerShell
in the NEW extracted Step16 folder containing dashboard.py** and upgrade your
existing installation. This includes a SQLite backup checkpoint fix:

```powershell
conda run -n opsfusion312 python -m opsfusion.upgrade --project "$env:USERPROFILE\Downloads\OpsFusion-AI-Step10\OpsFusion-AI-Step10"
```

Then change to your existing account folder and create a NEW migration backup:

```powershell
Set-Location "$env:USERPROFILE\Downloads\OpsFusion-AI-Step10\OpsFusion-AI-Step10"
conda run -n opsfusion312 python -m opsfusion.maintenance backup --project . --output-dir "$env:USERPROFILE\Downloads\OpsFusion-Backups" --offline
```

Keep the original installation. In a second Windows PowerShell window, upload
the exact generated backup filename:

```powershell
scp -i "$env:USERPROFILE\Downloads\YOUR-SSH-KEY.key" "$env:USERPROFILE\Downloads\OpsFusion-Backups\opsfusion-backup-EXACT-NAME.zip" ubuntu@YOUR-VM-IP:/home/ubuntu/
```

In the **VM terminal**, inside the Step16 code folder:

```bash
chmod 600 /home/ubuntu/opsfusion-backup-EXACT-NAME.zip
sudo python3 deploy/cloud.py restore --archive /home/ubuntu/opsfusion-backup-EXACT-NAME.zip
```

Restore streams bytes into an unprivileged one-off container and verifies the
trusted backup before promoting a new `/data/site`. A populated site or running
stack is rejected. Passwords, reports, reviews and evidence are preserved;
copied login sessions are removed. No private backup enters the Docker image.
The two sites do not synchronize: use the cloud as the active site after migration.

If you deliberately want **empty cloud history and a separate account**, skip
restore and instead run this once on the VM:

```bash
sudo python3 deploy/cloud.py bootstrap --username bharath
```

Enter a new password of 12+ characters; hidden typing is normal. Choose restore
OR bootstrap. Do not bootstrap after restore or repeat bootstrap on Windows.

## 7. Start the public site

```bash
sudo python3 deploy/cloud.py up
sudo python3 deploy/cloud.py status
```

Startup checks the persistent account store before exposing the gateway. Open
**https://YOUR-NAME.duckdns.org** and sign in with your restored account. Only
80/443 are published; the backend has a 4 GB RAM/2 CPU limit. The public
certificate requires correct DNS and reachable ports. A backend health pass
does not verify public TLS or browser login. For errors:

```bash
sudo python3 deploy/cloud.py logs
```

After public access succeeds, disconnect SSH; Docker continues running after
your laptop is off and restarts containers after a VM reboot. Do not stop the
VM or Docker. `cloud.py stop` deliberately leaves the stack stopped until `up`.
Never use `docker compose down -v` on volumes whose data you want to retain.

The gateway authenticates dashboard media, downloads and WebSockets. API writes
still need an explicit bearer token. Accounts share one site's data with role
permissions; this does not provide isolation between independent customers.

## 8. Export a cloud backup

Close streams/watches and stop camera/notification workers first. On the VM:

```bash
sudo python3 deploy/cloud.py stop
sudo python3 deploy/cloud.py backup
sudo python3 deploy/cloud.py up
```

Use the exact generated filename printed under `/data/backups`:

```bash
sudo python3 deploy/cloud.py export-backup --name opsfusion-backup-EXACT-NAME.zip --output /home/ubuntu/opsfusion-backup-EXACT-NAME.zip
sudo chown ubuntu:ubuntu /home/ubuntu/opsfusion-backup-EXACT-NAME.zip
```

Then download it from **Windows PowerShell** to a new destination filename:

```powershell
scp -i "$env:USERPROFILE\Downloads\YOUR-SSH-KEY.key" ubuntu@YOUR-VM-IP:/home/ubuntu/opsfusion-backup-EXACT-NAME.zip "$env:USERPROFILE\Downloads\OpsFusion-Backups\opsfusion-backup-EXACT-NAME.zip"
```

The VM export refuses replacement; scp can overwrite the Windows destination,
so check that filename yourself. Backups include private evidence/password hashes
and are not encrypted by this tool. Keep a private copy outside the VM. Restore
to a fresh empty site using step 6; do not erase the current volume as a shortcut.
The Compose project name `opsfusion-cloud` stays stable when code folders change.
No automatic deletion of incident data or backups is enabled.

## Completion and live inputs

Step16 finishes after the real server builds and public HTTPS works. Step17
checks permissions, end-to-end processing, persistence, reboot and backup restore
on that deployment. Your Step14 own-recording accuracy test remains deferred.

Previously uploaded recordings can run on the VM with your PC off. Fresh input
from a webcam or future sensor needs an independently powered and reachable
acquisition device. The cloud cannot read a powered-off laptop webcam or an
unreachable private RTSP feed. No hardware bridge has been added.

## Official references checked 2026-10-14

- https://www.oracle.com/cloud/free/
- https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/launchinginstance.htm
- https://docs.docker.com/engine/install/ubuntu/
- https://www.duckdns.org/about.jsp
- https://download.pytorch.org/whl/cpu/torch/
- https://download.pytorch.org/whl/cpu/torchvision/

Provider terms can change; use the current console eligibility/estimate. This
release does not provision resources, spend money, set DNS or upload private data.
