"""Evaluate Jev, the LLM baseline and the rules baseline on the labelled access-request set.

Every number comes from a real run. Responses are cached under ``cache/`` so re-runs and CI replay them without
calling any API (set ``ACCESSDESK_OFFLINE=1`` to require the cache). Writes eval/results.md and eval/results.json.

Headline metrics (the point of the project):
  * safe auto-approval rate: share of non-risky requests the policy auto-approves;
  * risky-auto-approve rate: share of risky requests wrongly auto-approved (must be near zero);
  * level accuracy: agreement of the model's "needed level" with the labelled correct level.
Plus: decision accuracy vs the labelled action, Brier score and a reliability table (calibration), latency
p50/p95, and cost per 1,000 requests from real token usage.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from accessdesk.backends import BackendError, make_backend
from accessdesk.backends.jev import PRICE_PER_M_INPUT as JEV_IN
from accessdesk.backends.llm import PRICE_PER_M_INPUT as LLM_IN
from accessdesk.backends.llm import PRICE_PER_M_OUTPUT as LLM_OUT
from accessdesk.cache import OfflineCacheMiss, offline
from accessdesk.directory import Directory
from accessdesk.policy import Policy
from accessdesk.types import AccessRequest, Assessment

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = ROOT / "cache"


@dataclass
class Row:
    item: dict[str, Any]
    assessment: Assessment
    action: str  # policy action: auto_approve | escalate | deny


def load_items() -> list[dict[str, Any]]:
    return [json.loads(line) for line in (DATA / "requests.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def to_request(item: dict[str, Any]) -> AccessRequest:
    return AccessRequest(
        requester=item["requester"],
        system=item["system"],
        level=item["level"],
        justification=item["justification"],
        duration_days=item["duration_days"],
        ticket=item.get("ticket", ""),
    )


def run_backend(
    name: str, items: list[dict[str, Any]], directory: Directory, policy: Policy
) -> tuple[dict[str, Row], dict[str, float], dict[str, int]]:
    """Assess every item a backend can answer, keyed by item id. Offline, items not in the cache are skipped
    (recorded as misses) rather than aborting, so a partially-cached baseline still contributes a fair subset."""
    backend = make_backend(name, cache_dir=CACHE)
    rows: dict[str, Row] = {}
    latencies: dict[str, float] = {}
    usage = {"input": 0, "output": 0, "calls": 0, "live_calls": 0, "misses": 0}
    for item in items:
        ctx = directory.enrich(to_request(item))
        start = time.perf_counter()
        try:
            a = backend.assess(ctx)
        except (BackendError, OfflineCacheMiss) as exc:
            if offline():
                usage["misses"] += 1
                continue
            raise SystemExit(f"{name}: {exc}. Run once online to populate the cache.") from exc
        elapsed = (time.perf_counter() - start) * 1000
        pre = policy.pre_check(ctx)
        decision = pre if pre is not None else policy.decide(a, ctx)
        assert decision is not None
        rows[item["id"]] = Row(item, a, decision.action)
        latencies[item["id"]] = a.latency_ms if a.cached else elapsed
        usage["calls"] += 1
        if not a.cached:
            usage["live_calls"] += 1
        usage["input"] += a.input_tokens
        usage["output"] += a.output_tokens
    return rows, latencies, usage


def brier(rows: list[Row]) -> tuple[float, list[tuple[float, float, int]]]:
    """Brier score and a 5-bin reliability table on 'is this request risky?'.

    Predicted risk probability = P(risk level >= 2), i.e. high or critical.
    """
    preds: list[tuple[float, int]] = []
    for r in rows:
        p = r.assessment.risk_probs[2] + r.assessment.risk_probs[3]
        preds.append((p, 1 if r.item["risky"] else 0))
    score = statistics.fmean((p - y) ** 2 for p, y in preds) if preds else 0.0
    bins: list[tuple[float, float, int]] = []
    for lo in (0.0, 0.2, 0.4, 0.6, 0.8):
        hi = lo + 0.2
        bucket = [(p, y) for p, y in preds if (lo <= p < hi or (hi == 1.0 and p == 1.0))]
        if bucket:
            bins.append((statistics.fmean(p for p, _ in bucket), statistics.fmean(y for _, y in bucket), len(bucket)))
        else:
            bins.append((lo + 0.1, float("nan"), 0))
    return score, bins


def metrics_for(name: str, rows: list[Row], latencies: list[float], usage: dict[str, int]) -> dict[str, Any]:
    n = len(rows)
    risky = [r for r in rows if r.item["risky"]]
    auto = [r for r in rows if r.action == "auto_approve"]
    should_auto = [r for r in rows if r.item["expected"] == "auto_approve"]

    # recall: of the requests that SHOULD be cleared without a person, how many were?
    recalled = sum(1 for r in should_auto if r.action == "auto_approve")
    risky_auto = sum(1 for r in risky if r.action == "auto_approve")
    decision_correct = sum(1 for r in rows if r.action == r.item["expected"])
    # level accuracy: only where a non-"none" correct level is defined
    level_items = [r for r in rows if r.item["correct_level"] != "none"]
    level_correct = sum(1 for r in level_items if r.assessment.needed_level == r.item["correct_level"])

    score, table = brier(rows)
    cost = cost_per_1000(name, usage)
    return {
        "n": n,
        "auto_approve_recall": recalled / len(should_auto) if should_auto else 0.0,
        "should_auto": len(should_auto),
        "risky_auto_approve_rate": risky_auto / len(risky) if risky else 0.0,
        "risky_auto_approved": risky_auto,
        "automation_rate": len(auto) / n if n else 0.0,
        "auto_approved": len(auto),
        "decision_accuracy": decision_correct / n if n else 0.0,
        "level_accuracy": level_correct / len(level_items) if level_items else 0.0,
        "brier": score,
        "reliability": table,
        "latency_p50_ms": round(statistics.median(latencies), 1) if latencies else 0.0,
        "latency_p95_ms": round(_pct(latencies, 95), 1),
        "cost_per_1000_usd": cost,
        "usage": usage,
    }


def cost_per_1000(name: str, usage: dict[str, int]) -> float:
    calls = usage["calls"] or 1
    if name == "jev":
        per_call = (usage["input"] / calls) * JEV_IN / 1_000_000
    elif name == "llm":
        per_call = ((usage["input"] / calls) * LLM_IN + (usage["output"] / calls) * LLM_OUT) / 1_000_000
    else:
        return 0.0
    return per_call * 1000


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def automation_at_thresholds(
    rows_by_backend: dict[str, list[Row]], policy: Policy, directory: Directory, items: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Automation rate and its accuracy as the auto-approve risk line is tightened/loosened (Jev only, by default)."""
    out: dict[str, list[dict[str, Any]]] = {}
    ctxs = [directory.enrich(to_request(it)) for it in items]
    for name, rows in rows_by_backend.items():
        series: list[dict[str, Any]] = []
        for line in (0.6, 0.9, 1.2, 1.5, 1.8):
            p = policy.with_thresholds(auto_approve_risk_below=line)
            autos = 0
            wrong_risky = 0
            for ctx, r in zip(ctxs, rows, strict=True):
                pre = p.pre_check(ctx)
                action = pre.action if pre is not None else p.decide(r.assessment, ctx).action
                if action == "auto_approve":
                    autos += 1
                    if r.item["risky"]:
                        wrong_risky += 1
            series.append(
                {
                    "risk_line": line,
                    "automation_rate": autos / len(rows),
                    "risky_auto_approved": wrong_risky,
                }
            )
        out[name] = series
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backends", default="jev,llm,rules")
    ap.add_argument("--policy", default=str(ROOT / "policies" / "default.yaml"))
    args = ap.parse_args()

    items = load_items()
    directory = Directory.load(DATA / "directory.yaml")
    policy = Policy.load(args.policy) if Path(args.policy).exists() else Policy()

    names = [n.strip() for n in args.backends.split(",")]
    raw = {name: run_backend(name, items, directory, policy) for name in names}
    coverage = {name: len(rows) for name, (rows, _, _) in raw.items()}

    # Compare every backend on the same items: those each backend could assess (the intersection). Offline, a
    # partially-cached baseline (e.g. the LLM on the free tier) contributes only its subset, kept fair this way.
    common_ids = set.intersection(*[set(rows.keys()) for rows, _, _ in raw.values()]) if raw else set()
    ordered_ids = [it["id"] for it in items if it["id"] in common_ids]

    results: dict[str, Any] = {
        "n_items": len(items),
        "n_evaluated": len(ordered_ids),
        "coverage": coverage,
        "backends": {},
    }
    rows_by_backend: dict[str, list[Row]] = {}
    for name in names:
        rows, latencies, usage = raw[name]
        sub_rows = [rows[i] for i in ordered_ids]
        sub_lat = [latencies[i] for i in ordered_ids]
        rows_by_backend[name] = sub_rows
        results["backends"][name] = metrics_for(name, sub_rows, sub_lat, usage)

    results["automation_curve"] = automation_at_thresholds(
        {k: v for k, v in rows_by_backend.items() if k == "jev"} or rows_by_backend, policy, directory, [it for it in items if it["id"] in common_ids]
    )

    (ROOT / "eval" / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    (ROOT / "eval" / "results.md").write_text(render_markdown(results), encoding="utf-8")
    update_readme(results)
    print(render_markdown(results))
    return 0


def update_readme(results: dict[str, Any]) -> None:
    """Keep the README's headline table in sync with the latest real run."""
    readme = ROOT / "README.md"
    if not readme.exists():
        return
    text = readme.read_text(encoding="utf-8")
    start, end = "<!-- EVAL_TABLE_START -->", "<!-- EVAL_TABLE_END -->"
    if start not in text or end not in text:
        return
    bk = results["backends"]
    order = [b for b in ("jev", "llm", "rules") if b in bk]
    rows = [
        ("Auto-approval recall (should-auto)", "auto_approve_recall", "{:.1%}"),
        ("Risky auto-approved (count, must be ~0)", "risky_auto_approved", "{}"),
        ("Overall automation rate", "automation_rate", "{:.1%}"),
        ("Needed-level accuracy", "level_accuracy", "{:.1%}"),
        ("Decision accuracy vs label", "decision_accuracy", "{:.1%}"),
        ("Calibration — Brier (lower better)", "brier", "{:.3f}"),
        ("Latency p50 / p95 (ms)", "_latency", "{}"),
        ("Cost per 1,000 requests", "cost_per_1000_usd", "${:.4f}"),
    ]
    table = ["| Metric | " + " | ".join(b.upper() for b in order) + " |", "| --- | " + " | ".join("---" for _ in order) + " |"]
    for label, key, fmt in rows:
        cells = []
        for b in order:
            if key == "_latency":
                cells.append(f"{bk[b]['latency_p50_ms']:.0f} / {bk[b]['latency_p95_ms']:.0f}")
            else:
                cells.append(fmt.format(bk[b][key]))
        table.append(f"| {label} | " + " | ".join(cells) + " |")
    n_eval = results.get("n_evaluated", results["n_items"])
    caption = f"_Latest run: all three backends compared on the {n_eval} requests each could assess (regenerate with `python eval/run_eval.py`):_"
    block = f"{start}\n\n{caption}\n\n" + "\n".join(table) + f"\n\n{end}"
    before = text.split(start)[0]
    after = text.split(end)[1]
    readme.write_text(before + block + after, encoding="utf-8")


def render_markdown(results: dict[str, Any]) -> str:
    bk = results["backends"]
    order = [b for b in ("jev", "llm", "rules") if b in bk]
    n_eval = results.get("n_evaluated", results["n_items"])
    cov = results.get("coverage", {})
    cov_note = ""
    if any(c < results["n_items"] for c in cov.values()):
        parts = ", ".join(f"{k} {v}/{results['n_items']}" for k, v in cov.items())
        cov_note = (
            f" All backends are compared on the **{n_eval} requests every backend could assess from its cache** "
            f"(per-backend coverage: {parts}). The LLM baseline runs on the free tier, which caps calls per day; "
            "top up its cache with `AI_API_KEY=... python eval/run_eval.py --backends llm` and re-run."
        )
    lines = [
        "# Access Desk evaluation",
        "",
        f"Labelled set: **{results['n_items']} invented access requests**; this run scored **{n_eval}** of them. "
        "Every number is from a real run; model responses are cached so this replays offline. Nothing is ever "
        "provisioned." + cov_note,
        "",
        "## Headline",
        "",
        "| Metric | " + " | ".join(b.upper() for b in order) + " |",
        "| --- | " + " | ".join("---" for _ in order) + " |",
    ]

    def row(label: str, key: str, pct: bool = True, fmt: str = "{:.1%}") -> str:
        cells = []
        for b in order:
            v = bk[b][key]
            cells.append(fmt.format(v) if pct else f"{v}")
        return f"| {label} | " + " | ".join(cells) + " |"

    lines.append(row("Auto-approval recall (of should-auto, higher is better)", "auto_approve_recall"))
    lines.append(row("Risky auto-approved (count, must be ~0)", "risky_auto_approved", pct=False))
    lines.append(row("Risky auto-approve rate (must be ~0)", "risky_auto_approve_rate"))
    lines.append(row("Overall automation rate", "automation_rate"))
    lines.append(row("Decision accuracy vs label", "decision_accuracy"))
    lines.append(row("Needed-level accuracy", "level_accuracy"))
    lines.append(row("Brier score (lower is better)", "brier", fmt="{:.3f}"))
    lines.append(row("Latency p50 (ms)", "latency_p50_ms", pct=False, fmt="{:.0f}") if False else _ms_row("Latency p50", "latency_p50_ms", bk, order))
    lines.append(_ms_row("Latency p95", "latency_p95_ms", bk, order))
    lines.append("| Cost per 1,000 requests (USD) | " + " | ".join(f"${bk[b]['cost_per_1000_usd']:.4f}" for b in order) + " |")

    lines += [
        "",
        "## How to read this",
        "",
        "- **Risky auto-approved** is the number that matters most: requests labelled risky "
        "(over-asking, role mismatch, vague, social-engineering, critical systems) that got approved with no "
        "person involved. The policy is tuned so this is zero or near it.",
        "- **Auto-approval recall** is the share of the requests that *should* be cleared without a person "
        "(safe, well-justified, time-boxed, fits the role) that actually were. That is the work the desk "
        "takes off the team.",
        "- **Needed-level accuracy** is how often the model picks the access level the task actually needs, "
        "which drives the least-privilege recommendations.",
        "",
    ]

    # calibration (jev)
    cal = bk.get("jev", bk[order[0]])["reliability"]
    lines += [
        "## Calibration (risk probability vs observed risky rate)",
        "",
        f"Backend: {('JEV' if 'jev' in bk else order[0].upper())}. Brier score {bk.get('jev', bk[order[0]])['brier']:.3f}.",
        "",
        "| Predicted risk | Observed risky | Requests |",
        "| --- | --- | --- |",
    ]
    for mean_p, obs, count in cal:
        obs_txt = "-" if count == 0 else f"{obs:.0%}"
        lines.append(f"| {mean_p:.0%} | {obs_txt} | {count} |")

    # automation curve
    lines += [
        "",
        "## Automation vs the auto-approve risk line (Jev)",
        "",
        "Loosening the risk line auto-approves more requests but risks approving a risky one. The default line keeps risky auto-approvals at zero.",
        "",
        "| Risk line | Automation rate | Risky auto-approved |",
        "| --- | --- | --- |",
    ]
    curve = results["automation_curve"]
    series = curve.get("jev") or next(iter(curve.values()))
    for pt in series:
        lines.append(f"| {pt['risk_line']:.1f} | {pt['automation_rate']:.1%} | {pt['risky_auto_approved']} |")

    # spend
    lines += ["", "## Spend", ""]
    for b in order:
        u = bk[b]["usage"]
        if b == "rules":
            lines.append(f"- **rules**: no API, no cost. {u['calls']} decisions.")
        else:
            lines.append(
                f"- **{b}**: {u['calls']} requests ({u['live_calls']} live, rest from cache), "
                f"{u['input']:,} input tokens, {u['output']:,} output tokens."
            )
    lines.append("")
    return "\n".join(lines)


def _ms_row(label: str, key: str, bk: dict[str, Any], order: list[str]) -> str:
    return f"| {label} (ms) | " + " | ".join(f"{bk[b][key]:.0f}" for b in order) + " |"


if __name__ == "__main__":
    raise SystemExit(main())
