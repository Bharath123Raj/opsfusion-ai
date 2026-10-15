"""One-inbox SMTP delivery over verified TLS. Credentials stay in the environment."""
from datetime import datetime, timezone
from email.headerregistry import Address
from email.errors import HeaderParseError
from email.message import EmailMessage
from email.utils import format_datetime
import hashlib
import ipaddress
import json
import os
import re
import smtplib
import ssl

ENV_PATTERN = r'OPSFUSION_[A-Z0-9_]{1,80}'


class EmailDeliveryError(Exception):
    def __init__(self, code, retryable=False, status=None):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.status = status


def _text(value, maximum=320):
    return isinstance(value, str) and 0 < len(value) <= maximum and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def _mailbox(value):
    # A single bare ASCII mailbox prevents header injection and partial-recipient retries.
    if not _text(value, 254) or not value.isascii() or any(c in value for c in ' ,;<>'):
        return False
    try:
        parsed = Address(addr_spec=value)
        return bool(parsed.username and parsed.domain and '.' in parsed.domain and parsed.addr_spec == value)
    except (ValueError, IndexError, HeaderParseError):
        return False


def email_settings(env_name, require_password=True):
    """Validate without echoing raw config or secrets into error messages."""
    error = 'Email settings are missing/invalid. Run tools/set_email_environment.ps1 in this terminal.'
    if not isinstance(env_name, str) or not re.fullmatch(ENV_PATTERN, env_name):
        raise ValueError('Use an OPSFUSION_ environment variable name')
    raw = os.environ.get(env_name, '')
    try:
        if not raw or len(raw) > 8192:
            raise ValueError()
        config = json.loads(raw)
        allowed = {'host', 'port', 'security', 'username', 'from', 'to', 'password_env'}
        if not isinstance(config, dict) or set(config) - allowed:
            raise ValueError()
        host = config.get('host')
        if not _text(host, 253) or not host.isascii():
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host) or any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-') for label in host.split('.')):
                raise ValueError()
        mode = config.get('security', 'starttls')
        port = config.get('port', 465 if mode == 'ssl' else 587)
        if mode not in ('starttls', 'ssl') or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError()
        if not _text(config.get('username')) or not _mailbox(config.get('from')) or not _mailbox(config.get('to')):
            raise ValueError()
        password_env = config.get('password_env', 'OPSFUSION_SMTP_PASSWORD')
        if not isinstance(password_env, str) or not re.fullmatch(ENV_PATTERN, password_env) or password_env == env_name:
            raise ValueError()
        if require_password and not _text(os.environ.get(password_env), 512):
            raise ValueError()
        return {'host': host.lower(), 'port': port, 'security': mode, 'username': config['username'],
                'from': config['from'], 'to': config['to'], 'password_env': password_env}
    except (ValueError, TypeError, KeyError):
        raise ValueError(error) from None


def email_target_hash(config):
    # Password rotation alone may retry an existing delivery; routing changes may not.
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _safe(value, maximum=200):
    return ''.join(c if ord(c) >= 32 and ord(c) != 127 else ' ' for c in str(value))[:maximum]


def build_message(config, payload, identifier):
    if not re.fullmatch('[0-9a-f]{64}', identifier):
        raise ValueError('Invalid delivery ID')
    message = EmailMessage()
    message['From'] = config['from']
    message['To'] = config['to']
    message['Message-ID'] = f'<{identifier}@opsfusion.local>'
    message['X-OpsFusion-Delivery-ID'] = identifier
    if payload.get('kind') == 'test':
        message['Subject'] = '[OpsFusion TEST] Email connection'
        tick = datetime.fromtimestamp(payload['requested_at'], timezone.utc)
        text = ('OpsFusion email connection test.\n\nNo incident was detected by this test.\n'
                'This message confirms only that your SMTP server accepted a test submission.\n')
    else:
        incident = payload['incident']
        demo = 'DEMO ' if incident['synthetic'] else ''
        message['Subject'] = f"[OpsFusion {demo}{_safe(incident['risk'], 20)}] {_safe(incident['type'], 60)} | {_safe(incident['asset_id'], 80)}"
        tick = datetime.fromisoformat(payload['observed_at'].replace('Z', '+00:00'))
        text = '\n'.join([
            'OpsFusion operational alert',
            'Source: ' + ('synthetic/demo' if incident['synthetic'] else 'not marked synthetic'),
            'Asset: ' + _safe(incident['asset_id']),
            'Incident: ' + _safe(incident['incident_id']),
            'Type: ' + _safe(incident['type']),
            'Priority: ' + _safe(incident['risk']),
            'Detected at (UTC): ' + _safe(incident['detected_at']),
            'Notice time (UTC): ' + _safe(payload['observed_at']),
            'Notice reason: ' + _safe(payload['reason']),
            'Cause status: ' + _safe(payload['cause_status']),
            'Evidence IDs: ' + ', '.join(_safe(x, 120) for x in payload['evidence_ids'][:50]),
            '', 'Review the saved incident in your local OpsFusion dashboard.',
            'An alert does not confirm a root cause. No machine-control action was executed.',
        ])
    message['Date'] = format_datetime(tick.astimezone(timezone.utc))
    message.set_content(text + '\nDelivery ID: ' + identifier + '\n'
                        'Delivery may repeat after connection loss; use this ID to identify duplicates.\n')
    return message


def send_email(config, payload, identifier):
    """Return SMTP acceptance, not inbox placement or human receipt."""
    client = None
    try:
        message = build_message(config, payload, identifier)
        context = ssl.create_default_context()
        if config['security'] == 'ssl':
            client = smtplib.SMTP_SSL(config['host'], config['port'], timeout=5, context=context)
            client.ehlo()
        else:
            client = smtplib.SMTP(config['host'], config['port'], timeout=5)
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        password = os.environ.get(config['password_env'])
        if not _text(password, 512):
            raise EmailDeliveryError('smtp_password_missing')
        client.login(config['username'], password)
        refused = client.send_message(message, from_addr=config['from'], to_addrs=[config['to']])
        if refused:
            # There is exactly one recipient, so this never retries a partially accepted batch.
            status = next(iter(refused.values()))[0]
            raise EmailDeliveryError('smtp_recipient_rejected', 400 <= status < 500, status)
        return 250
    except ssl.SSLError:
        raise EmailDeliveryError('smtp_tls_failed') from None
    except smtplib.SMTPNotSupportedError:
        raise EmailDeliveryError('smtp_tls_or_auth_unsupported') from None
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailDeliveryError('smtp_auth_failed', 400 <= exc.smtp_code < 500, exc.smtp_code) from None
    except smtplib.SMTPRecipientsRefused as exc:
        status = next(iter(exc.recipients.values()))[0]
        raise EmailDeliveryError('smtp_recipient_rejected', 400 <= status < 500, status) from None
    except smtplib.SMTPResponseException as exc:
        raise EmailDeliveryError('smtp_rejected', 400 <= exc.smtp_code < 500, exc.smtp_code) from None
    except smtplib.SMTPServerDisconnected:
        raise EmailDeliveryError('smtp_delivery_uncertain', True) from None
    except (OSError, smtplib.SMTPException):
        raise EmailDeliveryError('smtp_connection_failed', True) from None
    finally:
        # QUIT failure after DATA acceptance must not turn success into a resend.
        if client is not None:
            try:
                client.quit()
            except Exception:
                pass
            try:
                client.close()
            except Exception:
                pass
