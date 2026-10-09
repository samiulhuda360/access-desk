"""LLM baseline: an OpenAI-compatible chat model asked the same questions, answering in JSON."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, cast

import httpx

from ..cache import DiskCache, OfflineCacheMiss, key_for, offline
from ..questions import LEVEL_CRITERIA, LEVEL_INSTRUCTIONS, NOULS, RISK_INSTRUCTIONS, RISK_LEVELS, build_state
from ..types import LEVELS, Assessment, Level, RequestContext
from .base import BackendError, clamp, level_confidence, normalise

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
DEFAULT_MODEL = "gemini-flash-lite-latest"
# List price for the paid tier of the Flash-Lite model, USD per million tokens.
PRICE_PER_M_INPUT = 0.10
PRICE_PER_M_OUTPUT = 0.40

SYSTEM = (
    "You are an access-request reviewer for an IT and security team. You never grant anything; you only assess. "
    "Answer every question about the request in the given state. Reply with JSON only."
)


def build_prompt(state: dict[str, Any]) -> str:
    lines = ["State:", json.dumps(state, indent=2, default=str), "", "Questions (give a probability from 0 to 1 that the answer is yes):"]
    for name, q in NOULS.items():
        lines.append(f'- "{name}": {q["instructions"]} Yes means: {q["true"]} No means: {q["false"]}')
    lines += ["", f'"needed_level": {LEVEL_INSTRUCTIONS} One of:']
    lines += [f"  {name}: {text}" for name, text in LEVEL_CRITERIA.items()]
    lines += ['"needed_level_confidence": your probability that this level is right.', ""]
    lines += [f'"risk_level_probabilities": {RISK_INSTRUCTIONS} Give a probability for each level, in order:']
    lines += [f"  level {i}: {text}" for i, text in enumerate(RISK_LEVELS)]
    lines += [
        "",
        "Reply with exactly this JSON shape:",
        '{"justification_specific": 0.0, "fits_role": 0.0, "over_asking": 0.0, "touches_prod_pii": 0.0, '
        '"time_limited": 0.0, "needed_level": "read", "needed_level_confidence": 0.0, '
        '"risk_level_probabilities": [0.0, 0.0, 0.0, 0.0]}',
    ]
    return "\n".join(lines)


class LLMBackend:
    name = "llm"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float = 30.0,
        cache_path: str | Path | None = None,
        min_interval_s: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("AI_API_KEY", "")
        self.base_url = (base_url or os.environ.get("AI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("AI_MODEL") or DEFAULT_MODEL
        self._timeout = timeout_s
        self._client = client or httpx.Client(timeout=timeout_s)
        self._cache = DiskCache(cache_path) if cache_path else None
        interval = min_interval_s if min_interval_s is not None else float(os.environ.get("AI_MIN_INTERVAL", "2.5"))
        self._min_interval = max(2.5, interval)
        self._last_call = 0.0
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def payload(self, ctx: RequestContext) -> dict[str, Any]:
        return {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": build_prompt(build_state(ctx))}],
        }

    def assess(self, ctx: RequestContext) -> Assessment:
        payload = self.payload(ctx)
        key = key_for(payload)
        if self._cache is not None:
            hit = self._cache.get(key)
            if hit is not None:
                return parse(hit["response"], latency_ms=hit["latency_ms"], cached=True)
            if offline():
                raise OfflineCacheMiss("llm: no cached answer")
        if not self._api_key:
            raise BackendError("AI_API_KEY is not set")
        body, latency_ms = self._post(payload)
        parsed = parse(body, latency_ms=latency_ms, cached=False)  # validate before caching
        if self._cache is not None:
            self._cache.put(key, {"response": body, "latency_ms": latency_ms})
        return parsed

    def _post(self, payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        for attempt in range(4):
            with self._lock:
                wait = self._min_interval - (time.monotonic() - self._last_call)
                if wait > 0:
                    time.sleep(wait)
                self._last_call = time.monotonic()
            start = time.perf_counter()
            try:
                resp = self._client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers, timeout=self._timeout)
            except httpx.HTTPError as exc:
                if attempt == 3:
                    raise BackendError(f"llm unreachable: {type(exc).__name__}") from exc
                continue
            latency_ms = (time.perf_counter() - start) * 1000
            if resp.status_code in (429, 500, 503) and attempt < 3:
                time.sleep(15 * (attempt + 1))
                continue
            if resp.status_code != 200:
                raise BackendError(f"llm returned HTTP {resp.status_code}")
            return cast("dict[str, Any]", resp.json()), latency_ms
        raise BackendError("llm: retries exhausted")


def _extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise BackendError("llm reply had no JSON object")
    return cast("dict[str, Any]", json.loads(match.group(0)))


def parse(body: dict[str, Any], *, latency_ms: float, cached: bool) -> Assessment:
    try:
        content = body["choices"][0]["message"]["content"]
        data = _extract_json(content)
        risk_raw = data.get("risk_level_probabilities") or [0.25] * 4
        risk_probs = normalise([clamp(x) for x in list(risk_raw)[:4]] + [0.0] * max(0, 4 - len(risk_raw)))
        level = str(data.get("needed_level", "")).strip()
        if level not in LEVELS:
            level = "read"
        conf = clamp(data.get("needed_level_confidence", 0.5))
        rest = (1 - conf) / (len(LEVELS) - 1)
        level_probs = {str(name): (conf if name == level else rest) for name in LEVELS}
        usage = body.get("usage", {}) or {}
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise BackendError(f"unexpected llm response: {exc}") from exc
    return Assessment(
        needed_level=cast("Level", level),
        level_probs=level_probs,
        justification_specific=clamp(data.get("justification_specific")),
        fits_role=clamp(data.get("fits_role")),
        over_asking=clamp(data.get("over_asking")),
        touches_prod_pii=clamp(data.get("touches_prod_pii")),
        time_limited=clamp(data.get("time_limited")),
        risk_probs=risk_probs,
        backend="llm",
        confidence=level_confidence(level_probs),
        latency_ms=latency_ms,
        input_tokens=int(usage.get("prompt_tokens", 0)),
        output_tokens=int(usage.get("completion_tokens", 0)),
        cached=cached,
    )
