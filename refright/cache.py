"""SQLite cache for HTTP API responses (stdlib only, thread-safe)."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

DEFAULT_TTL = 7 * 86400  # 7 days


class Cache:
    def __init__(self, path: str | None = None, ttl: int = DEFAULT_TTL, enabled: bool = True):
        self.ttl = ttl
        self.enabled = enabled
        if path is None:
            path = str(Path.home() / ".cache" / "refright" / "cache.sqlite")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self.db.execute("CREATE TABLE IF NOT EXISTS cache (url TEXT PRIMARY KEY, ts REAL, body TEXT)")

    def get(self, url: str) -> str | None:
        if not self.enabled:
            return None
        with self._lock:
            row = self.db.execute("SELECT ts, body FROM cache WHERE url = ?", (url,)).fetchone()
        if row and time.time() - row[0] < self.ttl:
            return row[1]
        return None

    def put(self, url: str, body: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
                            (url, time.time(), body))
            self.db.commit()

    def close(self) -> None:
        with self._lock:
            self.db.close()
