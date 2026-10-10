"""Independent synthetic goldens. Never import the tested agent's resolver.

This is a development score, NOT an official Atira score. Correctness is checked
against what the portal actually received, not what the agent intended to send.
Expected answers are deliberately not part of cases.json or an HTTP endpoint.
"""
from decimal import Decimal, InvalidOperation


def expected_answer(case_name):
    refs = {"baseline": "SYN-RFQ-4401", "blocked": "SYN-RFQ-4402", "future-withdrawal": "SYN-RFQ-4403"}
    if case_name not in refs:
        from extra_cases import expected_answer as extra_expected
        return extra_expected(case_name)
    if case_name == "blocked":
        return {"tender_reference": refs[case_name], "decision": "no_bid", "currency": "EUR",
                "discount_percent": 0, "positions": [], "total_net": 0}
    # X-01: 12 pcs; 90 EUR; mass 0.4kg*4.50=1.80; scope base+mass;
    # setup 24/12=2; 3 cartons*2.50/12=.625.
    # r2((90+1.8)*.92 + 2 + .625) = 87.08; report surcharge=3.80, packaging=.63.
    # X-02: 2 sets*3=6pcs; 1100 USD /10*.80=88 EUR;
    # r2(88*.92 + 2 cartons*5/6) = 82.63; packaging=1.67.
    # Total = 12*87.08 + 6*82.63 = 1540.74. Stock 10<12; stock 6>=6.
    return {"tender_reference": refs[case_name], "decision": "bid", "currency": "EUR", "discount_percent": 8,
            "positions": [
                {"position": "X-01", "material_number": "SYN-MAT-AX", "quantity": 12, "base_unit_price": 90,
                 "unit_price": 87.08, "surcharge_per_unit": 3.80, "packaging_per_unit": .63,
                 "rebate_total": 0, "lead_time_weeks": 8},
                {"position": "X-02", "material_number": "SYN-MAT-BX", "quantity": 6, "base_unit_price": 88,
                 "unit_price": 82.63, "surcharge_per_unit": 0, "packaging_per_unit": 1.67,
                 "rebate_total": 0, "lead_time_weeks": 3},
            ], "total_net": 1540.74}


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        n = Decimal(str(value))
        return n if n.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _money(a, b):
    a, b = _number(a), _number(b)
    return a is not None and b is not None and abs(a - b) < Decimal("0.005")


def _integer(a, b):
    a, b = _number(a), _number(b)
    return a is not None and a == b and a == a.to_integral_value()


def evaluate_answer(answer, case_name):
    """Grade entered values with field-level diagnostics and adapted RFQ weights."""
    expected = expected_answer(case_name)
    answer = answer if isinstance(answer, dict) else {}
    checks = []

    def check(label, actual, wanted, weight, comparison=lambda a, b: a == b):
        ok = comparison(actual, wanted)
        checks.append({"field": label, "actual": actual, "expected": wanted,
                       "weight": weight, "points": weight if ok else 0, "ok": ok})

    eligible = answer.get("tender_reference") == expected["tender_reference"]
    if expected["decision"] == "no_bid":
        check("reference", answer.get("tender_reference"), expected["tender_reference"], 10)
        check("decision", answer.get("decision"), "no_bid", 35)
        check("positions", answer.get("positions"), [], 20)
        check("total_net", answer.get("total_net"), 0, 20, _money)
        check("discount", answer.get("discount_percent"), 0, 5, _money)
        check("currency", answer.get("currency"), "EUR", 10)
    else:
        check("reference", answer.get("tender_reference"), expected["tender_reference"], 5)
        check("decision", answer.get("decision"), "bid", 5)
        check("discount", answer.get("discount_percent"), expected["discount_percent"], 5, _money)
        check("total_net", answer.get("total_net"), expected["total_net"], 5, _money)
        rows = answer.get("positions")
        rows = rows if isinstance(rows, list) and all(isinstance(p, dict) for p in rows) else []
        ids = [p.get("position") for p in rows]
        valid_ids = all(isinstance(i, str) for i in ids)
        structural = valid_ids and len(ids) == len(set(ids)) and set(ids) == {"X-01", "X-02"}
        actual_rows = {p["position"]: p for p in rows} if valid_ids else {}
        # An omitted position must not erase completed fields of another one.
        # Ambiguous duplicate IDs receive no field credit for that ID.
        field_rows = {key: row for key, row in actual_rows.items() if ids.count(key) == 1}
        arithmetic = Decimal(0)
        for row in rows:
            q, price = _number(row.get("quantity")), _number(row.get("unit_price"))
            if q is None or price is None:
                arithmetic = None
                break
            arithmetic += q * price
        check("total_consistency", answer.get("total_net"), str(arithmetic) if arithmetic is not None else None,
              5, lambda a, b: structural and _money(a, b))
        weights = {"material_number": .20, "quantity": .15, "base_unit_price": .15,
                   "surcharge_per_unit": .10, "packaging_per_unit": .10, "rebate_total": .10,
                   "unit_price": .10, "lead_time_weeks": .10}
        for want in expected["positions"]:
            got = field_rows.get(want["position"], {})
            for field, proportion in weights.items():
                comparison = (lambda a, b: a == b) if field == "material_number" else (
                    _integer if field in ("quantity", "lead_time_weeks") else _money)
                check(f"{want['position']}.{field}", got.get(field), want[field], 32.5 * proportion, comparison)
        first = next(p for p in expected["positions"] if p["position"] == "X-01")
        check("withdrawal_quantity", actual_rows.get("X-01", {}).get("quantity"), first["quantity"], 5, _integer)
        check("index_window", actual_rows.get("X-01", {}).get("surcharge_per_unit"), first["surcharge_per_unit"], 5, _money)
        if valid_ids:
            extras = sum(pid not in {"X-01", "X-02"} for pid in ids)
            duplicates = len(ids) - len(set(ids))
            if extras or duplicates:
                checks.append({"field": "extra_or_duplicate_positions", "actual": extras + duplicates,
                               "expected": 0, "weight": 0, "points": -32.5 * (extras + duplicates), "ok": False})
        if answer.get("currency") != "EUR":
            checks.append({"field": "currency", "actual": answer.get("currency"), "expected": "EUR",
                           "weight": 0, "points": 0, "ok": False})
    score = max(0, round(sum(c["points"] for c in checks), 3))
    if any(not c["ok"] for c in checks) and score == 100:
        score = 99.999  # structural/currency checks cannot silently earn perfect quality
    return {"source": "synthetic developer score; not Atira", "quality": score,
            "eligible": eligible, "perfect": eligible and all(c["ok"] for c in checks), "checks": checks}


def evaluate_state(state, case_name):
    """A perfect computed answer without an actual portal receipt earns no submission score."""
    answer = state.get("submitted")
    scored = evaluate_answer(answer, case_name)
    received = bool(state.get("receipt")) and isinstance(answer, dict)
    if not received:
        scored.update(quality=0, eligible=False, perfect=False)
    scored["received"] = received
    return scored
