# Oracle Always Free processor for the Netlify portal

Selected on 2026-10-15. **The VM has not been created or tested yet.**
This runs only the Python recording worker. The web interface stays on Netlify;
Auth, jobs, reviews and private evidence stay in the existing Supabase project.
It does not use the older all-in-one Oracle/Caddy deployment instructions.

## 1. Create the account and VM

Sign up yourself at https://www.oracle.com/cloud/free/. Oracle requires identity
and card verification and may place a temporary verification hold. Do not share
card details, account passwords, private SSH keys or verification codes in chat.
Keep the account on Free Tier; do not upgrade to Pay As You Go.

In your home region, open **Compute → Instances → Create instance**:

| Setting | Value |
| --- | --- |
| Name | `opsfusion-worker` |
| Image | Ubuntu 24.04 LTS ARM64, Always Free eligible |
| Shape | `VM.Standard.A1.Flex` |
| OCPUs / RAM | **2 OCPUs / 12 GB**, only if none of your A1 quota is already allocated |
| Boot volume | 100 GB, default performance, within your remaining 200 GB combined free storage |
| Network | Public subnet, internet gateway and public IPv4; no NAT gateway or load balancer |
| SSH key | Generate/save the private key on your computer |
| Inbound access | TCP 22 from your current public IP `/32`; replace a broad SSH rule instead of leaving both |

No public application ports, custom domain, Oracle database or GPU are needed.
Keep outbound internet access for HTTPS downloads and Supabase. Verify the
shape and storage are within the console's current free allowance before
creation. A trial credit balance is not proof that a resource remains free.
If free capacity is unavailable, try another availability domain in your home
region or wait; do not substitute a paid shape.

Oracle can reclaim idle Always Free VMs. This provides independent processing,
not a guaranteed uninterrupted service. Do not generate artificial load to
avoid reclamation. If a VM stops, queued jobs remain in Supabase.

## 2. Connect from Windows PowerShell

Replace both placeholders with your saved key filename and VM public IP:

```powershell
ssh -i "$env:USERPROFILE\Downloads\YOUR-SSH-KEY.key" ubuntu@YOUR-VM-IP
```

Verify the SSH host fingerprint against the VM's console information. All
commands below run in this **Ubuntu SSH terminal**, not Windows or Conda.

## 3. Install Docker on the fresh Ubuntu VM

These commands follow Docker's Ubuntu repository installation. If the VM
already runs Docker or another application, review its existing installation
instead of replacing packages or firewall rules.

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git python3
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
git clone https://github.com/Bharath123Raj/opsfusion-ai.git
cd opsfusion-ai
```

No Docker group membership, inbound web port or GitHub credential is required
for this public repository.

## 4. Configure the private Supabase key

On your own computer, open the existing project's API keys page:
https://supabase.com/dashboard/project/blvorawnwbcnujnapmgg/settings/api-keys

Use a **backend secret key** beginning `sb_secret_`. Copy it only into the
hidden prompt in your SSH terminal below. Do not put it in GitHub, Netlify,
the frontend, shell command text, screenshots or chat. The helper accepts
modern secret keys; it deliberately rejects frontend publishable keys.

In the VM repository folder:

```bash
sudo python3 deploy/worker.py configure
```

The helper uses this installation's existing Supabase URL. It creates
`.env.cloud-worker` exclusively, owned by root with permissions `600`, and
refuses to replace an existing file. The file is ignored by Git. Its content
and the container environment are accessible to the VM/Docker administrator,
so keep that host private. It does not create any cloud resources.

## 5. Build and start

```bash
sudo python3 deploy/worker.py build
sudo python3 deploy/worker.py start
sudo python3 deploy/worker.py status
sudo python3 deploy/worker.py logs
```

The build can take several minutes. It checks PyTorch, torchvision's CPU
operator, OpenCV, scikit-learn and the checksum-pinned detector weights on the
actual host. Matching Python 3.12 ARM wheels were located in the published
indexes; an actual ARM container build still needs to pass here.

`status` should show the worker running. `logs` should show it waiting for jobs,
without credentials or a repeating restart loop. Then the portal should show
**Processing worker connected** within its next refresh. That heartbeat, rather
than a running container alone, verifies the worker reached Supabase.

The container runs as an unprivileged user, publishes no ports, limits itself
to 2 CPUs/4 GiB, rotates its logs and restarts after VM reboot. Temporary job
files and its worker ID use a Docker volume. Final reports and evidence use
Supabase. Your local `bharath` account and old runs are not migrated.

## 6. Verify the whole route

1. Sign in at https://opsfusion-ai-bharath.netlify.app/ with the enabled Google
   identity. Its origin must be in Supabase's Auth Redirect URLs.
2. Confirm the worker is connected. Upload a small controller CSV matching
   `docs/AUDIO_LOG_CONTRACT.md`; this is the quickest pipeline check.
3. Check the real progression **queued → running → succeeded**, inspect the
   reports, and download the private incident JSON and source evidence ZIP.
4. Save a review and reload to confirm it persists.
5. Disconnect SSH and shut down your original PC. From another device, submit
   another small job and verify it completes. The VM must remain running.
6. Test the controlled video with known entry/exit times separately. A successful
   deployment does not establish detector accuracy or hardware integration.

Do not report deployment complete before these live checks. Baseline training,
CPU/memory usage, private downloads and crash/reclaim behavior still need
verification on the VM. Monitor both providers' free quotas and storage usage;
the application does not automatically delete private evidence.

To stop the processor deliberately, preserving data:

```bash
sudo python3 deploy/worker.py stop
```

Do not use `down -v`: that deletes its local worker volume. To update later,
wait until there is no running job, stop the worker, `git pull --ff-only`, then
run `build`, `start`, `status` and `logs` again.

## Sources checked 2026-10-15

- https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- https://www.oracle.com/cloud/free/
- https://docs.docker.com/engine/install/ubuntu/
- https://download.pytorch.org/whl/cpu/torch/
- https://pypi.org/pypi/torch/2.5.1/json
- https://pypi.org/pypi/torchvision/0.20.1/json
