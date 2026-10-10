"""Approximate the challenge's quality score field by field (dev only).

usage: python dev/eval/score.py <answer.json> [--oracle dev/oracle/public-1.json]
Implements the RFQ scoring table of the platform docs (docs/reference/atira-llms-full.txt, "RFQ scoring") for bid and
no_bid oracles. The two optional checks are scored only when the oracle declares them under "checks", and they are
approximations: the withdrawn-Q&A check as "the affected position's quantity is right", the metal-index check as
"every position's surcharge_per_unit is right". Sandbox test runs remain the ground truth.
"""
import argparse, json
from decimal import Decimal as D, InvalidOperation
from pathlib import Path

FIELDS = {"material_number": .20, "quantity": .15, "base_unit_price": .15, "surcharge_per_unit": .10,
          "packaging_per_unit": .10, "rebate_total": .10, "unit_price": .10, "lead_time_weeks": .10}
CENT = D("0.01")


def _dec(value) -> D | None:
    try:
        return D(str(value))
    except (InvalidOperation, ValueError):
        return None


def eq(a, b) -> bool:
    """Numbers to the cent, everything else as stripped text. Unparseable answers score 0 instead of crashing."""
    if a is None or b is None:
        return False
    if isinstance(b, (int, float)) and not isinstance(b, bool):
        x = _dec(a)
        return x is not None and x.quantize(CENT) == D(str(b)).quantize(CENT)
    return str(a).strip() == str(b).strip()


def score(ans: dict, ora: dict) -> tuple[float, list[str]]:
    lines, pts = [], 0.0

    def add(name, ok, w):
        nonlocal pts
        pts += w if ok else 0
        lines.append(f"{'OK ' if ok else 'XX '} {name:<34} {w if ok else 0:>6.2f} / {w:.2f}")

    positions = [p for p in ans.get("positions") or [] if isinstance(p, dict)]
    if ora["decision"] == "no_bid":
        add("decision", eq(ans.get("decision"), "no_bid"), 75)
        add("tender_reference", eq(ans.get("tender_reference"), ora["tender_reference"]), 5)
        add("no positions", not positions, 5)
        add("total_net = 0", eq(ans.get("total_net"), 0), 15)
        return pts, lines

    add("decision", eq(ans.get("decision"), ora["decision"]), 5)
    add("tender_reference", eq(ans.get("tender_reference"), ora["tender_reference"]), 5)
    add("discount_percent", eq(ans.get("discount_percent"), ora["discount_percent"]), 5)
    add("total_net", eq(ans.get("total_net"), ora["total_net"]), 5)
    products = [(_dec(p.get("quantity")), _dec(p.get("unit_price"))) for p in positions]
    arith = all(q is not None and u is not None for q, u in products) and eq(
        sum((q * u for q, u in products), D(0)), ans.get("total_net"))
    add("arithmetic (total = Σ q×price)", arith, 5)

    pos = {str(p.get("position")): p for p in positions}
    opos = {p["position"]: p for p in ora["positions"]}
    checks = ora.get("checks") or {}
    if checks.get("withdrawn_position"):
        k = checks["withdrawn_position"]
        add("check: withdrawn Q&A quantity", eq(pos.get(k, {}).get("quantity"), opos[k]["quantity"]), 5)
    if checks.get("metal_index"):
        add("check: dated metal index", all(eq(pos.get(k, {}).get("surcharge_per_unit"), v["surcharge_per_unit"])
                                             for k, v in opos.items()), 5)
    share = (75 - 5 * (bool(checks.get("withdrawn_position")) + bool(checks.get("metal_index")))) / len(opos)
    for k, o in opos.items():
        a = pos.get(k, {})
        for f, w in FIELDS.items():
            add(f"{k} {f}", eq(a.get(f), o.get(f)), share * w)
    ids = [str(p.get("position")) for p in positions]
    extra = [k for k in ids if k not in opos] + [k for k in set(ids) if ids.count(k) > 1]
    if extra:
        pts -= share * len(extra)
        lines.append(f"XX  extra/duplicate positions {extra}: -{share * len(extra):.2f}")
    return pts, lines


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("answer"); ap.add_argument("--oracle", default=str(Path(__file__).parents[1] / "oracle/public-1.json"))
    a = ap.parse_args()
    ans = json.loads(Path(a.answer).read_text()); ans = ans.get("answer", ans) or {}
    total, lines = score(ans, json.loads(Path(a.oracle).read_text()))
    print("\n".join(lines)); print(f"\nQUALITY ≈ {total:.2f} / 100")
