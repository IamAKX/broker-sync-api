"""Thin, synchronous HTTP client for Equal Solution's (eqldata) vendor API —
just the 3 calls app/services/inception_vendor_sync_service.py needs: auth,
instrument list, and an EOD-range (OHLCV) fetch. Reimplemented directly here
rather than depending on the `eqldata` PyPI package (the one the standalone
backfill script in the sibling inception-stock-data repo uses), for two
reasons:

  1. That package is a client-side SDK far broader than the 3 endpoints this
     needs (it also wraps a realtime websocket feed) — pulling in a whole
     extra dependency for that would be overkill for a server-side "fetch
     since the last synced date" call.
  2. Its own get_EOD_range()/EqualDataError wrapper silently drops the
     retry_after_seconds field a 429 response includes — see that sibling
     repo's eod_backfill.py, whose own RateLimitError/fetch_eod_range call
     the range endpoint directly for exactly this reason (discovered the
     hard way during a real backfill). This module ports that same fix.

Synchronous (plain `requests`, not httpx) — the same library and behavior
already proven against this vendor's real API through eod_backfill.py's
actual backfill runs, rather than reimplementing against an async client
untested against this vendor's quirks. Callers running inside FastAPI's
async request handlers (inception_vendor_sync_service.py) MUST wrap these
in asyncio.to_thread() so a call never blocks the event loop.
"""

from __future__ import annotations

import csv
import io
import zipfile
from typing import Any

import requests

from app.exceptions import VendorApiError, VendorAuthError

_TIMEOUT_AUTH = 30
_TIMEOUT_INSTRUMENTS = 60
_TIMEOUT_RANGE = 900  # a ZIP download can be slow — matches eqldata's own get_EOD_range default


class VendorRateLimitError(VendorApiError):
    """429 from the EOD-range endpoint. retry_after_seconds is the vendor's
    own suggested wait, when it provides one (None otherwise) — see this
    module's docstring on why that field needs a direct request to see."""

    def __init__(self, message: str, retry_after_seconds: int | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def generate_auth_token(email: str, password: str, base_url: str) -> str:
    """Raises VendorAuthError on any non-200 response, a missing token in an
    otherwise-200 response, or a network failure."""
    url = base_url.rstrip("/") + "/client/client_api_tocken"
    try:
        response = requests.post(url, json={"email": email, "password": password}, timeout=_TIMEOUT_AUTH)
    except requests.RequestException as exc:
        raise VendorAuthError(f"Could not reach Equal Solution: {exc}") from exc

    if response.status_code != 200:
        raise VendorAuthError(
            f"Equal Solution login failed (HTTP {response.status_code}): {_error_detail(response)}"
        )

    token = response.json().get("token")
    if not token:
        raise VendorAuthError("Equal Solution login succeeded but returned no token")
    return token


def get_instrument_list(token: str, base_url: str) -> dict[str, list[str]]:
    """{exchange: ["EXCHANGE:SYMBOL", ...], ...} for every exchange this
    account has access to (eqldata returns plain strings, not dicts, per
    instrument — see inception-stock-data/eod_backfill.py's own note)."""
    url = base_url.rstrip("/") + "/client/get_instrument/"
    try:
        response = requests.post(
            url, headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT_INSTRUMENTS,
        )
    except requests.RequestException as exc:
        raise VendorApiError(f"Could not reach Equal Solution: {exc}") from exc

    if response.status_code != 200:
        raise VendorApiError(
            f"Equal Solution instrument list failed (HTTP {response.status_code}): {_error_detail(response)}"
        )

    data = response.json()
    if not isinstance(data, dict):
        raise VendorApiError(f"Unexpected instrument list response type: {type(data).__name__}")
    return {ex: [str(i) for i in items] for ex, items in data.items()}


def fetch_eod_range_rows(
    token: str, instruments: list[str], from_date: str, to_date: str, base_url: str,
) -> list[dict[str, str]]:
    """Downloads one (instruments, from_date, to_date) EOD-range request as
    a ZIP, parsed entirely in memory — no temp file, no export folder — and
    returns EOD_RANGE.csv's rows as dicts. Safe for this use case's bounded
    batch sizes (see inception_vendor_sync_service's chunking: <=500
    instruments, <=366 days per request, the vendor's own documented caps).

    Raises VendorRateLimitError (carrying retry_after_seconds when the
    vendor provides one) on a 429, VendorApiError on anything else
    (including a malformed/unexpected ZIP).
    """
    url = base_url.rstrip("/") + "/client/download_his_EOD_range"
    payload = {"auth_token": token, "instruments": instruments, "from_date": from_date, "to_date": to_date}
    try:
        response = requests.post(
            url, headers={"accept": "application/zip", "Content-Type": "application/json"},
            json=payload, timeout=_TIMEOUT_RANGE,
        )
    except requests.RequestException as exc:
        raise VendorApiError(f"Could not reach Equal Solution: {exc}") from exc

    if response.status_code == 429:
        body = _safe_json(response)
        raise VendorRateLimitError(
            f"Equal Solution rate/quota limit hit: {_error_detail(response, body)}",
            retry_after_seconds=body.get("retry_after_seconds"),
        )
    if response.status_code != 200:
        raise VendorApiError(
            f"Equal Solution EOD range fetch failed (HTTP {response.status_code}): {_error_detail(response)}"
        )

    if not response.content:
        return []

    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            with z.open("EOD_RANGE.csv") as f:
                wrapper = io.TextIOWrapper(f, encoding="utf-8", newline="")
                return list(csv.DictReader(wrapper))
    except (zipfile.BadZipFile, KeyError) as exc:
        raise VendorApiError(f"Equal Solution returned an unreadable EOD range response: {exc}") from exc


def _safe_json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _error_detail(response: requests.Response, body: dict[str, Any] | None = None) -> str:
    body = body if body is not None else _safe_json(response)
    return str(body.get("message") or body.get("detail") or body.get("code") or response.text[:300])
