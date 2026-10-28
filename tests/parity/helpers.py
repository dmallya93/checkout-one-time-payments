"""Assertion helpers shared by the parity tests."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import requests

TIMEOUT = 10


def content_type(response: requests.Response) -> tuple[str, str | None]:
    """Return ``(media type, charset)``; the media type lower-cased, the charset as sent."""
    header = response.headers.get("Content-Type")
    assert header is not None, "response has no Content-Type"
    media_type, *params = (part.strip() for part in header.split(";"))
    charset = None
    for param in params:
        name, _, value = param.partition("=")
        if name.strip().lower() == "charset":
            charset = value.strip().strip('"')
    return media_type.lower(), charset


def assert_content_type(
    response: requests.Response, media_type: str, charset: str | None = None
) -> None:
    """Media type must match; the charset too (case-insensitively, as RFC 9110 says)."""
    actual_type, actual_charset = content_type(response)
    assert actual_type == media_type, response.headers.get("Content-Type")
    if charset is None:
        assert actual_charset is None, response.headers.get("Content-Type")
    else:
        assert actual_charset is not None, response.headers.get("Content-Type")
        assert actual_charset.lower() == charset.lower(), response.headers.get("Content-Type")


def assert_json(response: requests.Response) -> Any:
    assert_content_type(response, "application/json", "utf-8")
    return response.json()


def shape(value: Any) -> Any:
    """Recursive structure of a JSON value (key sets and types), ignoring the values."""
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [shape(item) for item in value]
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if value is None:
        return "null"
    return type(value).__name__


def stripe_signature(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """Build a ``Stripe-Signature`` header with the documented ``t=...,v1=...`` scheme."""
    ts = int(time.time()) if timestamp is None else timestamp
    signed = f"{ts}.".encode() + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"
