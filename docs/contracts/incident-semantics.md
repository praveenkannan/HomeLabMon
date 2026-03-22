# Incident And Flap Semantics

This document defines when HomeLabMon opens, keeps, resolves, and suppresses incidents.

## Terms

- `evaluation`: one full probe round for one device check target.
- `check_key`: stable identifier such as `ping`, `tcp:22`, `http:https://...`, `dns:router.local`, or `temperature:cpu`.
- `fingerprint`: `device_id + check_key + reason_code`.
- `incident`: operator-facing record derived from repeated failing evaluations.

## Incident Eligibility

- Evaluations collected during `maintenance` or `snoozed` mode do not open new incidents.
- Existing open incidents remain open during maintenance or snooze unless manually closed.
- `UNKNOWN` observations do not open or resolve incidents by themselves.

## Open Rule

- Open an incident when the same fingerprint fails in `2` consecutive evaluations.
- Set `opened_at` from the second failing evaluation.
- Set `occurrence_count` to the total number of failed evaluations seen for that incident.
- Severity defaults:
  - `critical` for `ping`, `tcp`, `http`, and `HOT` temperature failures
  - `warning` for DNS failures and flap incidents

## Resolve Rule

- Resolve an incident when the same fingerprint passes in `2` consecutive evaluations.
- Set `resolved_at` from the second passing evaluation.
- If a different failure fingerprint appears while one is open, resolve the old incident with reason `superseded` and open a new incident when the new fingerprint satisfies the open rule.

## Flap Rule

- A fingerprint enters flap mode after `3` open transitions within a rolling `15 minute` window.
- While flap mode is active, do not create additional incident IDs for the same fingerprint.
- Instead, keep one incident with:
  - `reason_code: flap_detected`
  - `flapping: true`
  - `severity: warning`
- Flap mode clears after `15 consecutive minutes` of stable passing evaluations.
- `occurrence_count` continues increasing while flapping.

## Manual And System Resolution Codes

- `probe_failed`: probe completed and returned a failure result.
- `probe_timeout`: probe did not finish in the allowed time.
- `threshold_breach`: numeric threshold exceeded, including `HOT` temperature.
- `flap_detected`: repeated open and resolve churn on the same fingerprint.
- `superseded`: a new fingerprint replaced the current incident.
- `manual_close`: operator explicitly closed the incident.

## API Mapping

The incident payload returned by [`api-v1.md`](./api-v1.md) uses these fields:

```json
{
  "incident_id": "inc_01hv...",
  "device_id": "router",
  "check_key": "tcp:443",
  "reason_code": "probe_timeout",
  "state": "OPEN",
  "severity": "critical",
  "flapping": false,
  "opened_at": "2026-03-21T20:00:00Z",
  "last_seen_at": "2026-03-21T20:17:30Z",
  "resolved_at": null,
  "occurrence_count": 3
}
```

## Temperature Interaction

- `WARM` is advisory only and MUST NOT open an incident in v1.
- `HOT` is incident-eligible and uses `reason_code: threshold_breach`.
- `UNKNOWN` keeps the prior incident state unchanged.
