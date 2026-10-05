"""TTL cache of raw supplier responses with in-flight request de-duplication.

One cache instance is shared by every session when hosted, so a common part
(100nF 0603 X7R …) costs one API call per day no matter how many users ask.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable


class SearchCache:
    def __init__(self, directory: Path | None, ttl: float = 24 * 3600):
        self.dir = directory
        self.ttl = ttl
        self._mem: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def make_key(*parts: Any) -> str:
        blob = json.dumps(parts, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def get(self, key: str) -> Any | None:
        now = time.time()
        hit = self._mem.get(key)
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        if self.dir:
            path = self.dir / f"{key}.json"
            try:
                stored = json.loads(path.read_text("utf-8"))
                if now - stored["t"] < self.ttl:
                    self._mem[key] = (stored["t"], stored["v"])
                    return stored["v"]
            except (OSError, ValueError, KeyError):
                pass
        return None

    def put(self, key: str, value: Any) -> None:
        t = time.time()
        self._mem[key] = (t, value)
        if self.dir:
            path = self.dir / f"{key}.json"
            tmp = path.with_suffix(".tmp")
            try:
                tmp.write_text(json.dumps({"t": t, "v": value}), "utf-8")
                tmp.replace(path)
            except OSError:
                pass

    def get_or_fetch(self, key: str, fetch: Callable[[], Any]) -> Any:
        hit = self.get(key)
        if hit is not None:
            return hit
        # Only one thread fetches a given key; others wait and reuse its result.
        with self._lock_for(key):
            hit = self.get(key)
            if hit is not None:
                return hit
            value = fetch()
            self.put(key, value)
            return value

    def clear(self) -> None:
        self._mem.clear()
        if self.dir:
            for p in self.dir.glob("*.json"):
                p.unlink(missing_ok=True)
