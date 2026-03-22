#!/usr/bin/env python3
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_MACHINE = {
    'UNKNOWN': {'UNKNOWN': None, 'UP': None, 'DOWN': 'DOWN', 'DISABLED': None},
    'UP': {'UNKNOWN': None, 'UP': None, 'DOWN': 'DOWN', 'DISABLED': 'DISABLED'},
    'DOWN': {'UNKNOWN': None, 'UP': 'RECOVERED', 'DOWN': None, 'DISABLED': 'DISABLED'},
    'DISABLED': {'UNKNOWN': None, 'UP': 'ENABLED', 'DOWN': 'DOWN', 'DISABLED': None},
}
ALERT_EVENTS = {'DOWN', 'RECOVERED'}


def utc_now_dt():
    return datetime.now(timezone.utc)


def utc_now():
    return utc_now_dt().isoformat()


def parse_iso(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def classify_device_state(payload):
    if not isinstance(payload, dict):
        return 'UNKNOWN'
    reason = str(payload.get('reason', '')).strip().lower()
    if reason == 'disabled':
        return 'DISABLED'
    return 'UP' if bool(payload.get('healthy')) else 'DOWN'


def derive_event(previous_state, current_state):
    transitions = STATE_MACHINE.get(previous_state, STATE_MACHINE['UNKNOWN'])
    return transitions.get(current_state)


def _trim_transition_window(points, now_dt, window_seconds):
    if window_seconds <= 0:
        return []
    cutoff = now_dt - timedelta(seconds=window_seconds)
    kept = []
    for raw in points:
        dt = parse_iso(raw)
        if dt is None:
            continue
        if dt >= cutoff:
            kept.append(dt.isoformat())
    return kept


def _emit_hook(hook, name, incident, context):
    if not callable(hook):
        return
    try:
        hook(name, incident, context)
    except Exception:
        return


def derive_incidents(
    old_state,
    new_state,
    *,
    now_ts=None,
    engine_state=None,
    flap_window_seconds=900,
    flap_threshold=4,
    hook=None,
):
    now_ts = now_ts or utc_now()
    now_dt = parse_iso(now_ts) or utc_now_dt()

    previous_engine = engine_state if isinstance(engine_state, dict) else {}
    previous_devices = previous_engine.get('devices', {}) if isinstance(previous_engine.get('devices', {}), dict) else {}

    next_state = {'updated_at': now_ts, 'devices': {}}
    incidents = []

    for name in sorted(set(old_state.keys()) | set(new_state.keys())):
        previous_present = name in old_state
        previous = old_state.get(name, {})
        current = new_state.get(name, {})

        previous_class = classify_device_state(previous) if previous_present else 'UNKNOWN'
        current_class = classify_device_state(current)
        event = derive_event(previous_class, current_class)

        per_device_state = previous_devices.get(name, {}) if isinstance(previous_devices.get(name, {}), dict) else {}
        transitions = per_device_state.get('transition_times', [])
        if not isinstance(transitions, list):
            transitions = []
        transitions = _trim_transition_window(transitions, now_dt, flap_window_seconds)

        if event:
            transitions.append(now_dt.isoformat())
            transitions = _trim_transition_window(transitions, now_dt, flap_window_seconds)
            flap_count = len(transitions)
            flap_detected = event in ALERT_EVENTS and flap_count >= max(1, int(flap_threshold))
            hooks = ['transition']
            if flap_detected:
                hooks.append('flap_detected')

            incident = {
                'incident_id': f'{name}:{now_dt.isoformat()}:{event}',
                'timestamp': now_dt.isoformat(),
                'device': name,
                'event': event,
                'reason': current.get('reason', 'unknown') if isinstance(current, dict) else 'unknown',
                'from_state': previous_class,
                'to_state': current_class,
                'flap': {
                    'detected': flap_detected,
                    'transition_count': flap_count,
                    'window_seconds': int(flap_window_seconds),
                    'threshold': int(flap_threshold),
                },
                'hooks': hooks,
            }
            incidents.append(incident)

            _emit_hook(
                hook,
                'transition',
                incident,
                {'device': name, 'event': event, 'from_state': previous_class, 'to_state': current_class},
            )
            if flap_detected:
                _emit_hook(
                    hook,
                    'flap_detected',
                    incident,
                    {'device': name, 'count': flap_count, 'window_seconds': int(flap_window_seconds)},
                )

        next_state['devices'][name] = {
            'last_state': current_class,
            'last_seen': now_dt.isoformat(),
            'transition_times': transitions,
        }

    return incidents, next_state


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def save_json(path, payload):
    path_obj = Path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    tmp = path_obj.with_suffix(path_obj.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')
    tmp.replace(path_obj)


def main(argv=None):
    parser = argparse.ArgumentParser(description='derive incidents from monitor state snapshots')
    parser.add_argument('--old-state', required=True, help='path to previous status snapshot json')
    parser.add_argument('--new-state', required=True, help='path to current status snapshot json')
    parser.add_argument('--engine-state', default='', help='path to persisted incident engine state json')
    parser.add_argument('--write-engine-state', default='', help='write updated engine state to this path')
    parser.add_argument('--flap-window-seconds', type=int, default=900)
    parser.add_argument('--flap-threshold', type=int, default=4)
    args = parser.parse_args(argv)

    old_state = load_json(args.old_state, {})
    new_state = load_json(args.new_state, {})
    state = load_json(args.engine_state, {}) if args.engine_state else {}

    incidents, next_state = derive_incidents(
        old_state,
        new_state,
        engine_state=state,
        flap_window_seconds=args.flap_window_seconds,
        flap_threshold=args.flap_threshold,
    )

    if args.write_engine_state:
        save_json(args.write_engine_state, next_state)

    print(json.dumps({'incidents': incidents, 'engine_state': next_state}, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
