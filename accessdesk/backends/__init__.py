"""Decision backends: ``jev`` (TypeSafe System One API), ``llm`` (OpenAI-compatible chat model), ``rules`` (role matrix)."""

from __future__ import annotations

from pathlib import Path

from .base import Backend, BackendError
from .jev import JevBackend
from .llm import LLMBackend
from .rules import RulesBackend

__all__ = ["Backend", "BackendError", "JevBackend", "LLMBackend", "RulesBackend", "make_backend"]


def make_backend(name: str, *, timeout_s: float = 3.0, cache_dir: str | Path | None = None) -> Backend:
    cache = Path(cache_dir) if cache_dir else None
    if name == "jev":
        return JevBackend(timeout_s=timeout_s, cache_path=cache / "jev.json" if cache else None)
    if name == "llm":
        return LLMBackend(cache_path=cache / "llm.json" if cache else None)
    if name == "rules":
        return RulesBackend()
    raise ValueError(f"unknown backend {name!r}; use jev, llm or rules")
