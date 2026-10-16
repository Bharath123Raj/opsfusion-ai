# Card-free on-demand processing with Streamlit Community Cloud

This route replaces the selected Oracle VM because the owner has no payment
card. Netlify remains the upload/review interface and Supabase remains the
account, queue, report and private file store. **This Streamlit app is prepared,
not published or verified on Community Cloud yet.**

Community Cloud provides free app hosting with a GitHub account. It hibernates
after 12 hours without traffic and has shared CPU/memory limits. This console
processes **one job per click**; it does not run an unattended polling worker,
keep the free host awake artificially, or promise continuous monitoring.
Your original PC can be off when the cloud app is processing a recording.

## Deploy the app

1. Open https://share.streamlit.io/ and sign in with the GitHub account that can
   administer `Bharath123Raj/opsfusion-ai`.
2. Choose **Create app → Deploy a public app from GitHub** (the wording may
   vary). Set:

   | Field | Value |
   | --- | --- |
   | Repository | `Bharath123Raj/opsfusion-ai` |
   | Branch | `main` |
   | Main file | `cloud_processor/app.py` |
   | Python, in Advanced settings | **3.12** |

3. In **Advanced settings → Secrets**, use this structure and fill both empty
   values privately before deployment:

   ```toml
   OPSFUSION_SUPABASE_URL = "https://blvorawnwbcnujnapmgg.supabase.co"
   OPSFUSION_SUPABASE_SECRET_KEY = ""
   OPSFUSION_PROCESSOR_ACCESS_CODE = ""
   ```

   The backend key comes from the existing project's **Settings → API Keys**:
   https://supabase.com/dashboard/project/blvorawnwbcnujnapmgg/settings/api-keys
   Use a key beginning `sb_secret_`, not the browser's `sb_publishable_` key.
   Store it only in the processor's private hosting settings, never Netlify,
   source code, screenshots or chat.

   Generate a **separate random processor access code** on your computer, from
   any Windows PowerShell folder:

   ```powershell
   conda run -n opsfusion312 python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   Copy that generated code into `OPSFUSION_PROCESSOR_ACCESS_CODE` and keep a
   private copy for unlocking the console. It is different from your Google
   password, local `bharath` password and Supabase backend key. Only trusted
   administrators should know it: it permits starting jobs for this shared site.
   Do not paste either secret into this chat.

4. Click **Deploy**. This entrypoint's `requirements.txt` installs the pinned
   CPU-only perception dependencies; root `packages.txt` supplies OpenCV's
   Linux libraries. An empty/missing setting leaves the console disconnected.
5. Open the resulting `.streamlit.app` URL and unlock it with the processor
   access code. If available, restrict app visibility to yourself as another
   layer of access control. No invitations are sent by this code.

## Use and verify

1. Sign in at https://opsfusion-ai-bharath.netlify.app/ using the enabled Google
   identity. The Netlify origin must be allowed in Supabase Auth Redirect URLs.
2. Upload a **small controller CSV first** using its documented schema. It
   stays queued until you start processing; seeing no recent worker heartbeat
   while the console is idle is expected.
3. Open the processor, unlock it, and click **Process next queued job** once.
   Leave that page open while it runs. The oldest queued/reclaimable job for
   the site is processed, which may belong to another enabled site operator.
4. Confirm the job changes **queued → running → succeeded** in OpsFusion,
   inspect the evidence, download incident JSON and the ZIPs, and save a review.
   Reload the portal and confirm the review remains saved.
5. For another job, click again. To end access, click **Lock processor**. Each
   browser session must unlock separately; changing the configured access code
   invalidates existing unlocked session stamps.
6. Repeat from another device with your original PC off. This verifies cloud
   independence. If the app has hibernated, wake it before starting analysis.

The console does not expose a privileged client, queue access or start button
before unlock. A process-wide lock prevents overlapping clicks in the same
app process; the existing database lease protects against other processors.
Artifacts and reports are committed through the existing fenced worker path.
An interrupted run can be reclaimed after its 120-second lease expires, up to
the queue's existing three-attempt limit. A failed analysis is not reported as
successful. Failed jobs need corrected inputs submitted as a new job.

## Free demo limits

| Input | Limit for this processor |
| --- | --- |
| Video | 20 MiB; analyse at most 20 seconds at 1–5 FPS |
| Each CSV | 1 MiB |
| Each WAV | 5 MiB; existing 16-kHz mono requirements apply |
| All inputs combined | 40 MiB |

Sensor/audio still require separate reviewed normal training/calibration
recordings and valid clocks. The Netlify form also supports larger independently
hosted workers; inputs above these console limits fail clearly when claimed.
Start with logs, then a small video, and add baseline training only after the
actual host's resource use is checked. Community Cloud resources may be lower
than the documented maximum, so larger inference/training runs may fail.

Model initialization and a real job on the published host remain acceptance
checks. Automated tests use a scripted video detector for evidence plumbing;
they do not establish real detector accuracy. The controlled own-recording
test and any physical hardware validation remain separate.

The root Streamlit configuration now allows a hosting platform to choose its
listener. The supervised local `python -m opsfusion.service` still binds to
127.0.0.1 explicitly. For a direct local dashboard run, include
`--server.address 127.0.0.1`.

## References checked 2026-10-16

- https://streamlit.io/cloud
- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy
- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies
- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management
- https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app
- https://supabase.com/docs/guides/getting-started/api-keys
