"""Prometheus text-format metrics, with no extra dependency."""

from __future__ import annotations

import threading
from collections import defaultdict

BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
OUTCOMES = ("auto_approved", "approved", "escalated", "rejected", "denied")
ACTIONS = ("auto_approve", "escalate", "deny")


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.decisions: dict[str, int] = defaultdict(int)  # outcome -> count
        self.actions: dict[str, int] = defaultdict(int)  # policy action -> count
        self.backend_errors: dict[str, int] = defaultdict(int)
        self.fallbacks = 0
        self._bucket_counts = [0] * len(BUCKETS)
        self._sum = 0.0
        self._count = 0

    def record(self, *, outcome: str, action: str, latency_s: float, fallback: bool) -> None:
        with self._lock:
            self.decisions[outcome] += 1
            self.actions[action] += 1
            if fallback:
                self.fallbacks += 1
            self._sum += latency_s
            self._count += 1
            for i, b in enumerate(BUCKETS):
                if latency_s <= b:
                    self._bucket_counts[i] += 1

    def backend_error(self, backend: str) -> None:
        with self._lock:
            self.backend_errors[backend] += 1

    def render(self) -> str:
        with self._lock:
            out = [
                "# HELP accessdesk_decisions_total Final outcomes of access-request triage.",
                "# TYPE accessdesk_decisions_total counter",
            ]
            for outcome in OUTCOMES:
                out.append(f'accessdesk_decisions_total{{outcome="{outcome}"}} {self.decisions.get(outcome, 0)}')
            out += [
                "# HELP accessdesk_policy_actions_total Policy actions before any person is asked.",
                "# TYPE accessdesk_policy_actions_total counter",
            ]
            for action in ACTIONS:
                out.append(f'accessdesk_policy_actions_total{{action="{action}"}} {self.actions.get(action, 0)}')
            out += [
                "# HELP accessdesk_backend_errors_total Backend failures (timeouts, HTTP errors).",
                "# TYPE accessdesk_backend_errors_total counter",
            ]
            for backend in sorted(set(self.backend_errors) | {"jev"}):
                out.append(f'accessdesk_backend_errors_total{{backend="{backend}"}} {self.backend_errors.get(backend, 0)}')
            out += [
                "# HELP accessdesk_fallbacks_total Requests decided by the rules fallback because the model was unavailable.",
                "# TYPE accessdesk_fallbacks_total counter",
                f"accessdesk_fallbacks_total {self.fallbacks}",
                "# HELP accessdesk_triage_latency_seconds Time to decide, excluding time waiting for a person.",
                "# TYPE accessdesk_triage_latency_seconds histogram",
            ]
            for b, c in zip(BUCKETS, self._bucket_counts, strict=True):
                out.append(f'accessdesk_triage_latency_seconds_bucket{{le="{b}"}} {c}')
            out.append(f'accessdesk_triage_latency_seconds_bucket{{le="+Inf"}} {self._count}')
            out.append(f"accessdesk_triage_latency_seconds_sum {self._sum:.6f}")
            out.append(f"accessdesk_triage_latency_seconds_count {self._count}")
        return "\n".join(out) + "\n"
