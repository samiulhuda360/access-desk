# Access Desk evaluation

Labelled set: **243 invented access requests**; this run scored **141** of them. Every number is from a real run; model responses are cached so this replays offline. Nothing is ever provisioned. All backends are compared on the **141 requests every backend could assess from its cache** (per-backend coverage: jev 243/243, llm 141/243, rules 243/243). The LLM baseline runs on the free tier, which caps calls per day; top up its cache with `AI_API_KEY=... python eval/run_eval.py --backends llm` and re-run.

## Headline

| Metric | JEV | LLM | RULES |
| --- | --- | --- | --- |
| Auto-approval recall (of should-auto, higher is better) | 59.6% | 74.5% | 89.4% |
| Risky auto-approved (count, must be ~0) | 0 | 0 | 5 |
| Risky auto-approve rate (must be ~0) | 0.0% | 0.0% | 8.1% |
| Overall automation rate | 20.6% | 25.5% | 37.6% |
| Decision accuracy vs label | 73.8% | 78.7% | 76.6% |
| Needed-level accuracy | 91.4% | 95.7% | 86.2% |
| Brier score (lower is better) | 0.199 | 0.187 | 0.248 |
| Latency p50 (ms) | 230 | 1106 | 0 |
| Latency p95 (ms) | 310 | 15858 | 0 |
| Cost per 1,000 requests (USD) | $0.0436 | $0.1394 | $0.0000 |

## How to read this

- **Risky auto-approved** is the number that matters most: requests labelled risky (over-asking, role mismatch, vague, social-engineering, critical systems) that got approved with no person involved. The policy is tuned so this is zero or near it.
- **Auto-approval recall** is the share of the requests that *should* be cleared without a person (safe, well-justified, time-boxed, fits the role) that actually were. That is the work the desk takes off the team.
- **Needed-level accuracy** is how often the model picks the access level the task actually needs, which drives the least-privilege recommendations.

## Calibration (risk probability vs observed risky rate)

Backend: JEV. Brier score 0.199.

| Predicted risk | Observed risky | Requests |
| --- | --- | --- |
| 3% | 9% | 55 |
| 31% | 75% | 4 |
| 47% | 50% | 4 |
| 72% | 12% | 8 |
| 98% | 73% | 70 |

## Automation vs the auto-approve risk line (Jev)

Loosening the risk line auto-approves more requests but risks approving a risky one. The default line keeps risky auto-approvals at zero.

| Risk line | Automation rate | Risky auto-approved |
| --- | --- | --- |
| 0.6 | 9.9% | 0 |
| 0.9 | 9.9% | 0 |
| 1.2 | 20.6% | 0 |
| 1.5 | 21.3% | 0 |
| 1.8 | 21.3% | 0 |

## Spend

- **jev**: 243 requests (0 live, rest from cache), 252,220 input tokens, 37,422 output tokens.
- **llm**: 141 requests (0 live, rest from cache), 125,279 input tokens, 17,809 output tokens.
- **rules**: no API, no cost. 243 decisions.
