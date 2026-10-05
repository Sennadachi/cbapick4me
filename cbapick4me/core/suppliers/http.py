"""Small HTTP helper with retry/back-off for supplier APIs."""

from __future__ import annotations

import threading
import time

import requests

from .base import SupplierAuthError, SupplierError

USER_AGENT = "cbapick4me/0.1 (+BOM part picker)"


class Throttle:
    """Allow at most `per_minute` calls per rolling minute (shared across threads)."""

    def __init__(self, per_minute: int):
        self.interval = 60.0 / per_minute
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)


def request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    throttle: Throttle | None = None,
    retries: int = 4,
    timeout: float = 30,
    **kwargs,
) -> dict:
    kwargs.setdefault("headers", {}).setdefault("User-Agent", USER_AGENT)
    for attempt in range(retries + 1):
        if throttle:
            throttle.wait()
        try:
            resp = session.request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as e:
            if attempt == retries:
                raise SupplierError(f"Network error contacting {url}: {e}") from e
            time.sleep(2**attempt)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == retries:
                raise SupplierError(f"{url} returned HTTP {resp.status_code} after {retries} retries")
            retry_after = resp.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else 2**attempt
            except ValueError:
                wait = 2**attempt
            time.sleep(min(wait, 60))
            continue
        if resp.status_code in (401, 403):
            raise SupplierAuthError(f"API keys rejected ({resp.status_code}): {_error_text(resp)}")
        if not resp.ok:
            raise SupplierError(f"{url} returned HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()
        except ValueError as e:
            raise SupplierError(f"{url} returned invalid JSON") from e
    raise SupplierError("unreachable")


def _error_text(resp: requests.Response) -> str:
    """The supplier's own explanation, e.g. DigiKey's "Invalid clientId"."""
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:200] or resp.reason
    if isinstance(data, dict):
        parts = [data.get(k) for k in ("ErrorMessage", "ErrorDetails", "error_description", "error", "Message")]
        text = " — ".join(str(x) for x in parts if x)
        if text:
            return text
    return str(data)[:200]
