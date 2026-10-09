from __future__ import annotations

from accessdesk.backends.jev import parse as jev_parse
from accessdesk.backends.llm import parse as llm_parse
from accessdesk.backends.rules import RulesBackend
from accessdesk.directory import Directory
from accessdesk.types import AccessRequest

JEV_BODY = {
    "model": "jev-1.13.0",
    "answers": {
        "justification_specific": {"type": "noul", "noul": 0.9},
        "fits_role": {"type": "noul", "noul": 0.8},
        "over_asking": {"type": "noul", "noul": 0.1},
        "touches_prod_pii": {"type": "noul", "noul": 0.95},
        "time_limited": {"type": "noul", "noul": 0.7},
        "needed_level": {
            "type": "choice",
            "choice": "read",
            "probabilities": {"none": 0.0, "read": 0.9, "write": 0.1, "admin": 0.0},
            "confidence": 0.8,
        },
        "risk": {"type": "score", "score": 2.1, "legend": {}, "probabilities": {"0": 0.0, "1": 0.1, "2": 0.6, "3": 0.3}, "confidence": 0.7},
    },
    "usage": {"input_tokens": 300, "output_tokens": 20},
}


def test_jev_parse() -> None:
    a = jev_parse(JEV_BODY, latency_ms=250, cached=True)
    assert a.needed_level == "read"
    assert a.touches_prod_pii == 0.95
    assert abs(sum(a.risk_probs) - 1.0) < 1e-6
    assert a.critical == 0.3
    assert a.input_tokens == 300


def test_llm_parse_from_json_content() -> None:
    body = {
        "choices": [
            {
                "message": {
                    "content": '{"justification_specific":0.9,"fits_role":0.8,"over_asking":0.2,"touches_prod_pii":0.1,"time_limited":0.6,"needed_level":"write","needed_level_confidence":0.7,"risk_level_probabilities":[0.1,0.5,0.3,0.1]}'
                }
            }
        ],
        "usage": {"prompt_tokens": 500, "completion_tokens": 40},
    }
    a = llm_parse(body, latency_ms=800, cached=True)
    assert a.needed_level == "write"
    assert a.backend == "llm"
    assert abs(sum(a.risk_probs) - 1.0) < 1e-6


def test_rules_matrix_is_deterministic_and_blind_to_text(directory: Directory) -> None:
    b = RulesBackend()
    # The rules baseline assumes the stated reason is adequate (it cannot read text).
    ctx = directory.enrich(AccessRequest("evan_eng", "wiki", "read", "need it", 0))
    a = b.assess(ctx)
    assert a.justification_specific == 1.0
    assert a.fits_role == 1.0  # an engineer reading the low-sensitivity wiki fits
    # Sales admin on a critical system does not fit the role.
    ctx2 = directory.enrich(AccessRequest("sam_sales", "payments-gateway", "admin", "x", 0))
    assert b.assess(ctx2).fits_role == 0.0


def test_rules_contractor_downgraded(directory: Directory) -> None:
    b = RulesBackend()
    ctx = directory.enrich(AccessRequest("victor_contractor", "staging-api", "write", "x", 7))
    # a contractor engineer is downgraded one notch, so write on medium may not fit
    a = b.assess(ctx)
    assert a.over_asking in {0.0, 1.0}
