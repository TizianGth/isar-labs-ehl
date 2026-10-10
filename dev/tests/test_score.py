"""The dev scorer mirrors the platform's RFQ table (docs/reference/atira-llms-full.txt, "RFQ scoring")."""
import copy
import importlib.util
import json
from pathlib import Path

DEV = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("score", DEV / "eval/score.py")
score_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(score_mod)
ORACLE = json.loads((DEV / "oracle/public-1.json").read_text())


def test_oracle_scores_100_against_itself():
    pts, _ = score_mod.score(copy.deepcopy(ORACLE), ORACLE)
    assert round(pts, 6) == 100


def test_position_share_follows_the_declared_checks():
    ora = copy.deepcopy(ORACLE)
    ora["checks"] = {}
    pts, lines = score_mod.score(copy.deepcopy(ora), ora)
    assert round(pts, 6) == 100 and not any("check:" in line for line in lines)


def test_malformed_answer_scores_zero_instead_of_crashing():
    ans = copy.deepcopy(ORACLE)
    ans["total_net"] = "356.949,62"                       # German format where a number belongs
    ans["positions"][0]["unit_price"] = "abc"
    del ans["positions"][1]["quantity"]
    pts, lines = score_mod.score(ans, ORACLE)
    assert 0 < pts < 100
    assert any(line.startswith("XX  total_net") for line in lines)


def test_no_bid_table():
    ora = {"tender_reference": "XT-1", "decision": "no_bid", "currency": "EUR", "discount_percent": 0,
           "positions": [], "total_net": 0}
    right = {"tender_reference": "XT-1", "decision": "no_bid", "positions": [], "total_net": 0}
    assert score_mod.score(right, ora)[0] == 100
    wrong = {"tender_reference": "XT-1", "decision": "bid", "positions": [{"position": "1"}], "total_net": 10}
    assert score_mod.score(wrong, ora)[0] == 5         # only the reference keeps its credit
