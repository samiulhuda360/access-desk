"""Jev backend: every question about an access request in one typed call to the System One API."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, cast

import httpx

from ..cache import DiskCache, OfflineCacheMiss, key_for, offline
from ..questions import NOULS, build_state, jev_questions
from ..types import LEVELS, Assessment, Level, RequestContext
from .base import BackendError, clamp, level_confidence, normalise

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
PRICE_PER_M_INPUT = 0.042  # USD per million input tokens; output tokens are free


class JevBackend:
    name = "jev"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout_s: float = 3.0,
        cache_path: str | Path | None = None,
        client: httpx.Client | None = None,
        retries: int = 0,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("TYPESAFE_API_KEY", "")
        self._timeout = timeout_s
        self._client = client or httpx.Client(timeout=timeout_s)
        self._cache = DiskCache(cache_path) if cache_path else None
        self._retries = retries

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def payload(self, ctx: RequestContext) -> dict[str, Any]:
        return {"model": MODEL, "state": build_state(ctx), "questions": jev_questions()}

    def assess(self, ctx: RequestContext) -> Assessment:
        payload = self.payload(ctx)
        key = key_for(payload)
        if self._cache is not None:
            hit = self._cache.get(key)
            if hit is not None:
                return parse(hit["response"], latency_ms=hit["latency_ms"], cached=True)
            if offline():
                raise OfflineCacheMiss(f"jev: no cached answer for request {ctx.request.requester}->{ctx.request.system}")
        if not self._api_key:
            raise BackendError("TYPESAFE_API_KEY is not set")
        body, latency_ms = self._post(payload)
        if self._cache is not None:
            self._cache.put(key, {"response": body, "latency_ms": latency_ms})
        return parse(body, latency_ms=latency_ms, cached=False)

    def _post(self, payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            start = time.perf_counter()
            try:
                resp = self._client.post(API_URL, json=payload, headers=headers, timeout=self._timeout)
            except httpx.HTTPError as exc:
                last = exc
                continue
            latency_ms = (time.perf_counter() - start) * 1000
            if resp.status_code in (429, 529) and attempt < self._retries:
                time.sleep(0.5 * (attempt + 1))
                continue
            if resp.status_code != 200:
                raise BackendError(f"jev returned HTTP {resp.status_code}")
            return cast("dict[str, Any]", resp.json()), latency_ms
        raise BackendError(f"jev unreachable: {type(last).__name__ if last else 'rate limited'}")


def parse(body: dict[str, Any], *, latency_ms: float, cached: bool) -> Assessment:
    try:
        answers = body["answers"]
        risk = answers["risk"]["probabilities"]
        risk_probs = normalise([clamp(risk.get(str(i), 0.0)) for i in range(4)])
        lvl = answers["needed_level"]
        level_probs = {str(name): clamp(lvl["probabilities"].get(name, 0.0)) for name in LEVELS}
        nouls = {name: clamp(answers[name]["noul"]) for name in NOULS}
        usage = body.get("usage", {})
    except (KeyError, TypeError, AttributeError) as exc:
        raise BackendError(f"unexpected jev response: {exc}") from exc
    needed: Level = cast("Level", lvl["choice"] if lvl["choice"] in LEVELS else "read")
    return Assessment(
        needed_level=needed,
        level_probs=level_probs,
        justification_specific=nouls["justification_specific"],
        fits_role=nouls["fits_role"],
        over_asking=nouls["over_asking"],
        touches_prod_pii=nouls["touches_prod_pii"],
        time_limited=nouls["time_limited"],
        risk_probs=risk_probs,
        backend="jev",
        confidence=clamp(lvl.get("confidence", level_confidence(level_probs))),
        latency_ms=latency_ms,
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        cached=cached,
    )
