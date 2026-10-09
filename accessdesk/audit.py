"""Append-only JSONL audit log: one line per decision and per approval answer."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()

    def write(self, event: str, **fields: Any) -> None:
        if self.path is None:
            return
        record = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "event": event, **fields}
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # "a" mode only ever appends; the log is never rewritten or truncated by the gate.
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())

    def read(self) -> list[dict[str, Any]]:
        if self.path is None or not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
