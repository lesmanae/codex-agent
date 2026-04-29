"""Tiny JWT-style bearer token + PIN auth for the API.

We don't pull in PyJWT; HS256 is just `HMAC-SHA256(b64(header)+"."+b64(payload))`.
Tokens contain a single subject (`sub`) and an expiry (`exp`). Validation:
HMAC matches + not expired.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

ALGO = "HS256"


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def issue_token(*, subject: str, secret: str, ttl_seconds: int) -> tuple[str, int]:
    """Return (token, expires_at_unix_ts)."""
    now = int(time.time())
    exp = now + max(60, ttl_seconds)
    header = {"alg": ALGO, "typ": "JWT"}
    payload = {"sub": subject, "iat": now, "exp": exp}
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h}.{p}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    s = _b64url(sig)
    return f"{h}.{p}.{s}", exp


def verify_token(token: str, secret: str) -> dict | None:
    """Return decoded payload dict on success, None otherwise."""
    try:
        h, p, s = token.split(".", 2)
    except ValueError:
        return None
    signing_input = f"{h}.{p}".encode("ascii")
    expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        actual = _b64url_decode(s)
    except Exception:  # noqa: BLE001
        return None
    if not hmac.compare_digest(expected, actual):
        return None
    try:
        payload = json.loads(_b64url_decode(p))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        return None
    return payload


def pin_matches(submitted: str, expected: str) -> bool:
    """Constant-time comparison of submitted vs configured PIN."""
    if not submitted or not expected:
        return False
    return hmac.compare_digest(submitted.encode("utf-8"), expected.encode("utf-8"))
