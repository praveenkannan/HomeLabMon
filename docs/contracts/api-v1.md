# HomeLabMon API v1

Base path: `/api/v1`

This contract is intentionally small. v1 covers dashboard metadata, device status, incidents, device actions, and AI chat token minting.

## Versioning

- Path versioning is authoritative: `/api/v1/...`.
- Every success and error body MUST include `"api_version": "1.0"`.
- Response header `X-API-Version: 1.0` SHOULD be set on every response.
- Additive fields are allowed in v1 if existing fields keep meaning and optionality.
- Breaking changes require a new path version such as `/api/v2`.
- Clients MUST ignore unknown response fields.

## Common Rules

- Content type: `application/json; charset=utf-8`
- Timestamps: RFC 3339 UTC strings.
- IDs are opaque strings unless otherwise noted.
- Request correlation: clients MAY send `X-Request-Id`; server echoes it or generates one.
- Error responses MUST match [`error-schema.json`](./error-schema.json).

Success envelope:

```json
{
  "api_version": "1.0",
  "request_id": "req_01hv...",
  "generated_at": "2026-03-21T20:18:00Z",
  "data": {}
}
```

Unless explicitly marked otherwise, all endpoint "Response" examples below are full HTTP response bodies using this envelope (not just `data` fragments).

## Pagination

- List endpoints use cursor pagination.
- Query params:
  - `limit`: integer, default `50`, minimum `1`, maximum `100`
  - `cursor`: opaque token from the previous page's `next_cursor`
- Paginated response shape:

```json
{
  "api_version": "1.0",
  "request_id": "req_01hv...",
  "generated_at": "2026-03-21T20:18:00Z",
  "data": {
    "items": [],
    "page": {
      "limit": 50,
      "returned": 50,
      "next_cursor": "eyJvZmZzZXQiOjUwfQ"
    }
  }
}
```

- `next_cursor: null` means end of collection.
- Cursors are server-generated and MUST be treated as opaque.

## Payload Budgets

Budgets apply to uncompressed UTF-8 JSON.

| Endpoint | Request max | Response max |
| --- | ---: | ---: |
| `GET /meta` | 0.5 KiB | 4 KiB |
| `GET /devices` | 1 KiB | 256 KiB |
| `GET /devices/{device_id}` | 0.5 KiB | 64 KiB |
| `GET /incidents` | 1 KiB | 256 KiB |
| `GET /incidents/{incident_id}` | 0.5 KiB | 16 KiB |
| `POST /devices/{device_id}/actions` | 2 KiB | 4 KiB |
| `POST /ai/token` | 1 KiB | 4 KiB |
| `POST /ai/chat` | 8 KiB | 32 KiB |

If a request exceeds budget, return `413 payload_too_large`.

## Schemas

`DeviceSummary`

```json
{
  "id": "pi-monitor",
  "display_name": "Pi Monitor",
  "status": "UP",
  "maintenance_mode": "active",
  "heat_state": "NORMAL",
  "last_check_at": "2026-03-21T20:17:42Z",
  "open_incident_count": 0,
  "available_actions": ["run_check_now", "restart", "snooze"]
}
```

`DeviceDetail`

```json
{
  "id": "pi-monitor",
  "display_name": "Pi Monitor",
  "host": "monitor.example.invalid",
  "logo_url": "./www/assets/pi.svg",
  "status": "DEGRADED",
  "maintenance_mode": "snoozed",
  "heat": {
    "state": "WARM",
    "value_c": 63.5,
    "sampled_at": "2026-03-21T20:17:42Z",
    "thresholds": {
      "warm_c": 60,
      "hot_c": 75,
      "source": "global"
    }
  },
  "checks": [
    {
      "check_key": "tcp:22",
      "status": "PASS",
      "observed_at": "2026-03-21T20:17:40Z",
      "latency_ms": 8
    }
  ],
  "open_incidents": ["inc_01hv..."]
}
```

`Incident`

```json
{
  "incident_id": "inc_01hv...",
  "device_id": "router",
  "check_key": "http:https://router.example.invalid/health",
  "reason_code": "probe_failed",
  "state": "OPEN",
  "severity": "critical",
  "flapping": false,
  "opened_at": "2026-03-21T20:00:00Z",
  "last_seen_at": "2026-03-21T20:17:30Z",
  "resolved_at": null,
  "occurrence_count": 3
}
```

## Endpoints

### `GET /api/v1/meta`

Returns deploy-time metadata and feature flags.

`data` schema:

```json
{
  "instance_name": "homelab-dev",
  "site_title": "HomeLabMon Dev Dashboard",
  "dashboard_public_url": "https://monitor.example.invalid/status.html",
  "timezone": "America/Los_Angeles",
  "api_version": "1.0",
  "features": {
    "ai_chat_enabled": false,
    "advanced_animations": true,
    "restart_actions_enabled": false
  }
}
```

### `GET /api/v1/devices`

Lists device summaries.

Query params:

- `limit`, `cursor`
- `status`: optional enum `UP|DEGRADED|DOWN|UNKNOWN|MAINTENANCE|SNOOZED`
- `maintenance_mode`: optional enum `active|maintenance|snoozed`

Response: paginated `DeviceSummary[]`

### `GET /api/v1/devices/{device_id}`

Returns one `DeviceDetail`.

Errors:

- `404 device_not_found`

### `GET /api/v1/incidents`

Lists incidents.

Query params:

- `limit`, `cursor`
- `state`: optional enum `OPEN|RESOLVED`
- `device_id`: optional device filter
- `flapping`: optional boolean filter

Response: paginated `Incident[]`

### `GET /api/v1/incidents/{incident_id}`

Returns one `Incident`.

Errors:

- `404 incident_not_found`

### `POST /api/v1/devices/{device_id}/actions`

Queues an operator action for one device.

Request body:

```json
{
  "action": "snooze",
  "reason": "Patch window",
  "snooze_until": "2026-03-21T22:00:00Z"
}
```

Rules:

- `action` MUST be one of `restart|run_check_now|snooze|maintenance_on|maintenance_off`.
- `snooze_until` is required only for `snooze`.
- Unsupported or disabled actions return `409 action_not_allowed`.

Response:

```json
{
  "api_version": "1.0",
  "request_id": "req_01hv...",
  "generated_at": "2026-03-21T20:18:00Z",
  "data": {
    "action_id": "act_01hv...",
    "device_id": "pi-monitor",
    "status": "accepted"
  }
}
```

### `POST /api/v1/ai/token`

Mints a short-lived token for one `POST /api/v1/ai/chat` call.

Request body:

```json
{
  "scope": "ai:chat",
  "conversation_id": "dash_7b3c9b4f"
}
```

Response:

```json
{
  "api_version": "1.0",
  "request_id": "req_01hv...",
  "generated_at": "2026-03-21T20:18:00Z",
  "data": {
    "token": "<signed-token>",
    "token_type": "Bearer",
    "expires_at": "2026-03-21T20:20:00Z",
    "kid": "ed25519-2026-03",
    "scope": "ai:chat"
  }
}
```

Token semantics are defined in [`ai-token-contract.md`](./ai-token-contract.md).

### `POST /api/v1/ai/chat`

Runs one AI-assisted response using a token minted by `/api/v1/ai/token`.

Headers:

- `Authorization: Bearer <signed-token>`

Request body:

```json
{
  "messages": [
    {
      "role": "user",
      "content": "Why is the router marked degraded?"
    }
  ],
  "device_ids": ["router"]
}
```

Response:

```json
{
  "api_version": "1.0",
  "request_id": "req_01hv...",
  "generated_at": "2026-03-21T20:18:00Z",
  "data": {
    "answer": "The router health URL has failed three consecutive probes.",
    "model": "ops-assistant-v1",
    "fallback_used": false
  }
}
```

Errors:

- `401 invalid_token`
- `409 replay_detected`
- `429 ai_rate_limited`
