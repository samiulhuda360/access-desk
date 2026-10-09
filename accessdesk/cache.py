"""A small JSON disk cache so evaluation runs and CI replay recorded model responses."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any


class OfflineCacheMiss(RuntimeError):
    """Raised when ``ACCESSDESK_OFFLINE=1`` and a request is not in the cache."""


def offline() -> bool:
    return os.environ.get("ACCESSDESK_OFFLINE", "") in {"1", "true", "yes"}


def key_for(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class DiskCache:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def get(self, key: str) -> Any | None:
        return self._data.get(key)

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)

    def __len__(self) -> int:
        return len(self._data)
