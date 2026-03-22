#!/usr/bin/env python3
import base64
import hashlib
import hmac
import json
import time
from pathlib import Path


class TokenVerificationError(RuntimeError):
    def __init__(self, message, error_code='invalid_token', status_code=401, retryable=False):
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.retryable = retryable


class FileReplayCache:
    def __init__(self, path):
        self.path = Path(path)

    def _load(self):
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, sort_keys=True), encoding='utf-8')
        tmp.replace(self.path)

    def reserve(self, jti, expires_at, now=None):
        now = int(time.time() if now is None else now)
        state = {key: value for key, value in self._load().items() if isinstance(value, int) and value > now}
        if state.get(jti, 0) > now:
            self._save(state)
            return False
        state[jti] = int(expires_at)
        self._save(state)
        return True


def _decode_segment(segment):
    padding = '=' * (-len(segment) % 4)
    return base64.urlsafe_b64decode(f'{segment}{padding}'.encode('ascii'))


def _decode_json(segment):
    try:
        return json.loads(_decode_segment(segment).decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise TokenVerificationError(f'invalid token segment: {exc}') from exc


def _scope_matches(claim_value, required_scope):
    if isinstance(claim_value, str):
        return required_scope in claim_value.split()
    if isinstance(claim_value, list):
        return required_scope in claim_value
    return False


def verify_signed_token(
    token,
    signing_keys,
    expected_issuer,
    expected_audience,
    required_scope,
    replay_cache,
    now=None,
    max_ttl_seconds=300,
    clock_skew_seconds=30,
):
    parts = token.split('.')
    if len(parts) != 3:
        raise TokenVerificationError('token must have three parts')

    header = _decode_json(parts[0])
    claims = _decode_json(parts[1])
    if header.get('alg') != 'HS256':
        raise TokenVerificationError('unsupported signing algorithm')
    kid = header.get('kid')
    key = signing_keys.get(kid)
    if not kid or not key:
        raise TokenVerificationError('unknown signing key')

    signing_input = f'{parts[0]}.{parts[1]}'.encode('ascii')
    expected_sig = hmac.new(key.encode('utf-8'), signing_input, hashlib.sha256).digest()
    provided_sig = _decode_segment(parts[2])
    if not hmac.compare_digest(expected_sig, provided_sig):
        raise TokenVerificationError('signature verification failed')

    required_claims = {'iss', 'aud', 'iat', 'exp', 'jti', 'scope'}
    missing = sorted(claim for claim in required_claims if claim not in claims)
    if missing:
        raise TokenVerificationError(f'missing claims: {",".join(missing)}')

    current = int(time.time() if now is None else now)
    try:
        issued_at = int(claims['iat'])
        expires_at = int(claims['exp'])
    except (TypeError, ValueError) as exc:
        raise TokenVerificationError('iat and exp must be integers') from exc

    if claims['iss'] != expected_issuer or claims['aud'] != expected_audience:
        raise TokenVerificationError('issuer or audience mismatch')
    if not _scope_matches(claims['scope'], required_scope):
        raise TokenVerificationError('scope mismatch')
    if issued_at > current + clock_skew_seconds:
        raise TokenVerificationError('token issued in the future')
    if expires_at <= issued_at:
        raise TokenVerificationError('token expiry must be after issue time')
    if expires_at - issued_at > max_ttl_seconds:
        raise TokenVerificationError('token ttl exceeds maximum')
    if expires_at < current - clock_skew_seconds:
        raise TokenVerificationError('token expired')

    if not replay_cache.reserve(str(claims['jti']), expires_at + clock_skew_seconds, now=current):
        raise TokenVerificationError('token replay detected', error_code='replay_detected', status_code=409)

    return claims
