"""Approximate the challenge's quality score field by field (dev only).

usage: python dev/eval/score.py <answer.json> [--oracle dev/oracle/public-1.json]
Implements the bid-case table from docs/01-challenge.md. The two "checks" are approximations: the withdrawn-Q&A check
is scored as "the position whose quantity the oracle marks as withdrawn-affected is right", the metal-index check as
"every position's surcharge_per_unit is right". Sandbox test runs remain the ground truth.
"""
import argparse, json
from decimal import Decimal as D
from pathlib import Path

FIELDS = {"material_number": .20, "quantity": .15, "base_unit_price": .15, "surcharge_per_unit": .10,
          "packaging_per_unit": .10, "rebate_total": .10, "unit_price": .10, "lead_time_weeks": .10}
WITHDRAWN_CHECK_POSITION = "01.05"   # public-1 only (dev); the private set has its own

def eq(a, b) -> bool:
    if a is None or b is None:
        return False
    if isinstance(b, (int, float)) and not isinstance(b, bool):
        return D(str(a)).quantize(D("0.01")) == D(str(b)).quantize(D("0.01"))
    return str(a).strip() == str(b).strip()

def score(ans: dict, ora: dict) -> tuple[float, list[str]]:
    lines, pts = [], 0.0
    def add(name, ok, w):
        nonlocal pts
        pts += w if ok else 0
        lines.append(f"{'OK ' if ok else 'XX '} {name:<34} {w if ok else 0:>6.2f} / {w:.2f}")
    add("decision", ans.get("decision") == ora["decision"], 5)
    add("tender_reference", ans.get("tender_reference") == ora["tender_reference"], 5)
    add("discount_percent", eq(ans.get("discount_percent"), ora["discount_percent"]), 5)
    add("total_net", eq(ans.get("total_net"), ora["total_net"]), 5)
    pos = {p["position"]: p for p in ans.get("positions", [])}
    arith = eq(sum(D(str(p["quantity"])) * D(str(p["unit_price"])) for p in ans.get("positions", [])),
               ans.get("total_net"))
    add("arithmetic (total = Σ q×price)", arith, 5)
    opos = {p["position"]: p for p in ora["positions"]}
    w_check = opos.get(WITHDRAWN_CHECK_POSITION)
    add("check: withdrawn Q&A quantity", bool(w_check) and eq(pos.get(WITHDRAWN_CHECK_POSITION, {}).get("quantity"),
                                                               w_check["quantity"]), 5)
    add("check: dated metal index", all(eq(pos.get(k, {}).get("surcharge_per_unit"), v["surcharge_per_unit"])
                                         for k, v in opos.items()), 5)
    share = (75 - 10) / len(opos)
    for k, o in opos.items():
        a = pos.get(k, {})
        for f, w in FIELDS.items():
            add(f"{k} {f}", eq(a.get(f), o.get(f)), share * w)
    ids = [p["position"] for p in ans.get("positions", [])]
    extra = [k for k in ids if k not in opos] + [k for k in set(ids) if ids.count(k) > 1]
    if extra:
        pts -= share * len(extra)
        lines.append(f"XX  extra/duplicate positions {extra}: -{share * len(extra):.2f}")
    return pts, lines

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("answer"); ap.add_argument("--oracle", default=str(Path(__file__).parents[1] / "oracle/public-1.json"))
    a = ap.parse_args()
    ans = json.loads(Path(a.answer).read_text()); ans = ans.get("answer", ans)
    total, lines = score(ans, json.loads(Path(a.oracle).read_text()))
    print("\n".join(lines)); print(f"\nQUALITY ≈ {total:.2f} / 100")
