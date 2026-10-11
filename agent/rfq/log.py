"""Structured JSON logging with run-local secret redaction."""
import json
import time

_T0 = time.monotonic()
_SECRETS = set()
_PRIVATE_KEYS = {'password', 'api_key', 'token', 'tan', 'authorization', 'eval_token'}


def register_secret(value):
    if value is not None and str(value):
        _SECRETS.add(str(value))


def elapsed() -> float:
    return time.monotonic() - _T0


def _clean(value, key=''):
    if key.lower() in _PRIVATE_KEYS and value is not None:
        return '[redacted]'
    if isinstance(value, dict):
        return {str(k): _clean(v, str(k)) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_clean(v) for v in value]
    if isinstance(value, str):
        for secret in _SECRETS:
            if len(secret) >= 4:
                value = value.replace(secret, '[redacted]')
            elif value == secret:
                value = '[redacted]'
        return value
    return value


def log(event: str, **fields) -> None:
    line = {'t': round(elapsed(), 2), 'event': event, **_clean(fields)}
    print(json.dumps(line, ensure_ascii=False, default=str), flush=True)
