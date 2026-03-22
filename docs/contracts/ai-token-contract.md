# AI Signed Token Contract

HomeLabMon v1 uses a short-lived signed bearer token for AI chat. Tokens are minted by `POST /api/v1/ai/token` and consumed by `POST /api/v1/ai/chat`.

## Token Format

- Format: compact JWT JWS
- Header:

```json
{
  "alg": "EdDSA",
  "kid": "ed25519-2026-03",
  "typ": "JWT"
}
```

- Signature algorithm: Ed25519 (`EdDSA`)
- `kid` is mandatory and selects the active verification key.

## Required Claims

```json
{
  "iss": "homelab-dev",
  "aud": "homelabmon-ai",
  "sub": "dashboard:dash_7b3c9b4f",
  "scope": "ai:chat",
  "jti": "01HVQ6R5T2TR5H3V0M4QF6S2YB",
  "iat": 1774124340,
  "nbf": 1774124340,
  "exp": 1774124460,
  "ver": "1.0"
}
```

Claim rules:

- `iss`: HomeLabMon `instance_name`
- `aud`: fixed string `homelabmon-ai`
- `sub`: dashboard session or operator principal
- `scope`: fixed string `ai:chat`
- `jti`: globally unique token identifier
- `iat`, `nbf`, `exp`: NumericDate values in seconds
- `ver`: API contract version, fixed `1.0`

Optional claims:

- `device_ids`: array of device IDs the chat request may reference
- `ip_hash`: optional client binding hint if the deployment wants soft binding

## TTL

- Default TTL: `120` seconds
- Maximum TTL: `300` seconds
- Allowed clock skew: `30` seconds
- Tokens outside `nbf - skew` and `exp + skew` are invalid

## Replay Guard

- Tokens are single-use.
- The verifier MUST persist each accepted `jti` until `exp + 30 seconds`.
- A second use of the same `jti` MUST fail with `409 replay_detected`.
- Minting MUST use at least `128 bits` of entropy for `jti`.

## Rotation

- Exactly one signing key is active at a time.
- Tokens MUST include `kid`.
- Verifiers MUST trust the active key and the immediately previous key.
- The previous key MUST remain available for at least `max_ttl + skew + 60 seconds`.
- Rotation cadence SHOULD be at most `90 days` and MUST support emergency key replacement.
- v1 does not require remote JWKS discovery; key distribution is deployment-local.

## Validation Rules

Reject the token if any check fails:

- signature invalid
- missing required claim
- `aud != homelabmon-ai`
- `scope != ai:chat`
- `ver != 1.0`
- token expired or not yet valid
- `jti` already consumed

## API Response Shape

`POST /api/v1/ai/token` returns:

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
