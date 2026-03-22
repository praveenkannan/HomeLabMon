#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

from runtime_config import dashboard_url as resolve_dashboard_url
from runtime_config import runtime_root

BASE = runtime_root()
ALERT_STATE_PATH = BASE / 'state' / 'alert_state.json'


def utc_now_dt():
    return datetime.now(timezone.utc)


def utc_now():
    return utc_now_dt().isoformat()


def dashboard_url():
    return resolve_dashboard_url()


def detail_url(device_name: str):
    safe = quote(device_name or '', safe='')
    return f"{dashboard_url()}#device={safe}" if safe else dashboard_url()


def load_alert_state():
    if not ALERT_STATE_PATH.exists():
        return {'alerts': {}}
    try:
        return json.loads(ALERT_STATE_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {'alerts': {}}


def save_alert_state(payload):
    ALERT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ALERT_STATE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')


def cooldown_minutes():
    raw = os.getenv('HOMELABMON_ALERT_COOLDOWN_MINUTES', os.getenv('PI_MONITOR_ALERT_COOLDOWN_MINUTES', '15')).strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 15.0


def dedupe_key(name, state, reason, summary_type, device):
    raw = f"{name}|{state}|{reason}|{summary_type}|{device or ''}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def in_cooldown(record, now_dt, cooldown_min):
    if cooldown_min <= 0:
        return False
    last_sent = record.get('last_sent_at') if isinstance(record, dict) else None
    if not last_sent:
        return False
    try:
        last_dt = datetime.fromisoformat(last_sent)
    except Exception:
        return False
    age_minutes = (now_dt - last_dt).total_seconds() / 60.0
    return age_minutes < cooldown_min


def render_body(name, state, reason, summary_type, device, incident_id='', flap=''):
    lines = [
        f"[{state}] {name}",
        reason,
        '',
        f"dashboard={dashboard_url()}",
        f"device_detail={detail_url(device or name)}",
        f"summary_type={summary_type}",
        f"sent_at={utc_now()}",
    ]
    if incident_id:
        lines.append(f"incident_id={incident_id}")
    if flap:
        lines.append(f"flap={flap}")
    return '\n'.join(lines)


def send_telegram(text):
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        return False
    body = urlencode({'chat_id': chat_id, 'text': text}).encode()
    request = Request(f'https://api.telegram.org/bot{token}/sendMessage', data=body)
    with urlopen(request, timeout=10):
        return True


def send_email(subject, body):
    host = os.getenv('SMTP_HOST', '')
    port = int(os.getenv('SMTP_PORT', '587'))
    username = os.getenv('SMTP_USERNAME', '')
    password = os.getenv('SMTP_PASSWORD', '')
    to_addr = os.getenv('ALERT_EMAIL_TO', '')
    from_addr = os.getenv('ALERT_EMAIL_FROM', username)
    if not all([host, username, password, to_addr]):
        return False

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = from_addr
    msg['To'] = to_addr
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=15) as server:
        server.starttls(context=context)
        server.login(username, password)
        server.send_message(msg)
    return True


def parse_args(argv):
    parser = argparse.ArgumentParser(description='send monitor alert with dedupe/cooldown')
    parser.add_argument('name')
    parser.add_argument('state')
    parser.add_argument('reason')
    parser.add_argument('--summary-type', choices=['event', 'daily', 'weekly'], default='event')
    parser.add_argument('--device', default='')
    parser.add_argument('--incident-id', default='')
    parser.add_argument('--flap', default='')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--force', action='store_true')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    dry_run = args.dry_run or os.getenv('HOMELABMON_ALERT_DRY_RUN', os.getenv('PI_MONITOR_ALERT_DRY_RUN', '0')) == '1'
    now_dt = utc_now_dt()
    subject = f"[{args.state}] {args.name}"
    body = render_body(args.name, args.state, args.reason, args.summary_type, args.device, args.incident_id, args.flap)

    state = load_alert_state()
    alerts = state.setdefault('alerts', {})
    key = dedupe_key(args.name, args.state, args.reason, args.summary_type, args.device)
    record = alerts.get(key, {})

    if not args.force and in_cooldown(record, now_dt, cooldown_minutes()):
        print(f"suppressed_by_cooldown key={key[:10]} cooldown_min={cooldown_minutes()}")
        record['suppressed_count'] = int(record.get('suppressed_count', 0)) + 1
        record['last_suppressed_at'] = utc_now()
        alerts[key] = record
        save_alert_state(state)
        return 0

    if dry_run:
        print('dry_run=1; alert not sent')
        print(subject)
        print(body)
        sent_ok = True
    else:
        sent_ok = False
        try:
            sent_ok = send_telegram(body) or sent_ok
        except Exception as exc:
            print(f'telegram_send_failed={exc}', file=sys.stderr)
        try:
            sent_ok = send_email(subject, body) or sent_ok
        except Exception as exc:
            print(f'email_send_failed={exc}', file=sys.stderr)

    if sent_ok:
        alerts[key] = {
            'name': args.name,
            'state': args.state,
            'summary_type': args.summary_type,
            'device': args.device,
            'incident_id': args.incident_id,
            'flap': args.flap,
            'last_sent_at': utc_now(),
            'send_count': int(record.get('send_count', 0)) + 1,
            'suppressed_count': int(record.get('suppressed_count', 0)),
        }
        save_alert_state(state)
        return 0

    print('no alert transport configured or all transports failed', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
