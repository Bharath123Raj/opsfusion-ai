# SMTP email delivery — v0.11.0

Use START_HERE.md for the Windows workflow. Admins configure and test one inbox
per email destination. Preview destinations never send. Operators/viewers can read
delivery history but cannot configure/queue/run delivery. The separate worker
handles SMTP and HTTPS destinations; the dashboard performs no network sends.

## Configuration

Run tools/set_email_environment.ps1 separately in both dashboard and worker
terminals. OPSFUSION_EMAIL_SETTINGS contains JSON with host, port, security,
username, from, to, password_env. OPSFUSION_SMTP_PASSWORD holds the SMTP secret.
Example settings structure (placeholder addresses, never real credentials):

```json
{"host":"smtp.gmail.com","port":587,"security":"starttls","username":"sender@example.com","from":"sender@example.com","to":"recipient@example.com","password_env":"OPSFUSION_SMTP_PASSWORD"}
```

No password is accepted inside this JSON. Only bare ASCII email addresses and one
recipient are supported; display names, lists and international SMTPUTF8 addresses
are rejected. One-recipient delivery avoids partial batch acceptance. Multiple
inboxes can be separate destinations, explicitly enabled.

TLS modes: starttls upgrades before authentication; ssl uses TLS immediately.
Both verify the certificate and hostname using the OS/Python CA store.
Unencrypted delivery, self-signed production trust bypass and OAuth are not supported.
Gmail App Password availability depends on the account. Google requires 2-Step
Verification; organization policies, security-key-only configurations or Advanced
Protection can prevent availability. Another provider must support SMTP password
authentication; some managed Microsoft services require OAuth instead.

SQLite stores destination channel/environment name, a routing fingerprint and
immutable minimal incident payloads. It excludes email addresses, host, username,
password and raw provider replies. Those values remain in each hosting process's
environment, accessible to trusted local OS administrators. Exports likewise exclude
routing/credentials. Email itself necessarily contains From/To addresses.
Password rotation preserves queued deliveries; a change of host/port/TLS mode/
username/from/to/password-variable name requires deliberate reconfiguration.

## Activation, test, and contents

Activation applies to notices created after the settings save. Old previews/notices
are not sent. Changing/pausing a destination cancels pending/in-flight records;
a submission already in transit cannot be recalled.

Queue test email creates a durable negative-notice-ID record and audit entry bound
to the admin. It creates no incident and does not move the incident cursor. One
pending/in-flight test per destination revision is reused on repeated clicks.
Completed/failed tests can be followed by an explicit new test. Synthetic-incident
permission is independent from this connection probe.

Emails use the notice's frozen first/reopened report, never later facts. Plain text
includes asset, incident/category/priority, detected/notice times, cause status and
up to 50 evidence IDs. Causes remain unconfirmed. No raw media, local paths or
attachments are transmitted. Synthetic sources are blocked by default; explicitly
allowed ones carry DEMO. Tests carry TEST and say no incident was detected.

## Attempts and limits

Delivered with SMTP 250 means the submission server accepted the message. It is
not a delivery receipt, inbox/spam placement guarantee, bounce tracker or proof of
human reading. A disconnect after DATA acceptance can leave delivery uncertain.
A stable Message-ID/delivery ID survives retry; email servers need not deduplicate it.

SMTP 4xx/connection failures retry after 5, 10 and 20 seconds, at most four attempts.
SMTP 5xx and invalid TLS/configuration are permanent. Five-second socket timeouts
bound individual operations; DNS/the entire conversation lack a strict overall
deadline. The queue retains its 60-second lease and at-least-once crash semantics.
QUIT failure after accepted message data does not turn success into a resend.

Keep the worker running. No service installation, bounce handling, digest/grouping,
provider-quota awareness, OAuth refresh or HTML/media attachments are included.
Separate destinations may receive duplicates by design. Sessions expire within
eight hours; the usual worker duration is one hour.

## Troubleshooting

| Error/state | Check |
|---|---|
| destination_not_configured | Load settings/password in BOTH terminals; use the same values |
| endpoint_changed | Routing changed after queueing; save correct settings and queue a new test |
| smtp_auth_failed / 535 | Check SMTP policy, username and App Password; OpsFusion password is different |
| smtp_tls_failed | Check host/port/TLS, CA trust, clock and network interception; verification stays on |
| smtp_tls_or_auth_unsupported | Required TLS/authentication is unavailable; use a supported submission service |
| smtp_recipient_rejected / 5xx | Check recipient address, sender permissions and provider policy |
| smtp_rejected / 4xx | Temporary provider rejection; retries are capped |
| smtp_connection_failed | Check internet/firewall/server/port; worker retries |
| smtp_delivery_uncertain | Connection dropped; retry can duplicate an accepted message |
| pending / zero attempts | Start worker from SAME installation folder; refresh delivery status |
| suppressed | Synthetic incident blocked or old exact snapshot unavailable |
| cancelled | Settings were saved/paused while work was pending |
| delivered / no email | Check spam, address and provider status; no inbox guarantee |

Production TLS is always enforced. Tests explicitly trust a PUBLIC test-only
localhost certificate/key in tests/smtp_certificate_fixture.py. The application
never uses that fixture or trusts it for production servers. No credentials/provider
reply text appear in delivery logs/exports.

## Primary references

- [Python 3.12 smtplib](https://docs.python.org/3.12/library/smtplib.html)
- [Google App Password requirements](https://support.google.com/accounts/answer/185833?hl=en)
- [Google SMTP host/STARTTLS port](https://support.google.com/mail/answer/7104828?hl=en)

Software/loopback checks prove adapter behavior, not remote provider acceptance
or incident accuracy. Your own test inbox completes provider validation.
