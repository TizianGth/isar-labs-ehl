"""Compare what the portal recorded (the review page, extracted by the model) with the answer we computed.

Pure functions, unit-tested. Values on the page are raw strings in the page's number format; the default locale is
German (the portals' format), explicit locale tags win.
"""
import re
from decimal import Decimal

from ..rules.parse import clean, norm_id, number

CRITICAL = ("material_number", "quantity", "unit_price")
OPTIONAL = ("base_unit_price", "surcharge_per_unit", "packaging_per_unit", "rebate_total", "lead_time_weeks")


def _num(raw) -> Decimal | None:
    """JSON numbers as they are; explicit locale tags win; '1234.50' (decimal point with cents) is a plain decimal;
    everything else is read in the portals' German format ('1.234,50', '1.234' = 1234)."""
    if not isinstance(raw, str):
        return number(raw)
    if re.search(r"\((?:de|en|fr)-[A-Z]{2}\)", raw):
        return number(raw)
    core = re.sub(r"[^\d.,-]", "", raw)
    if re.fullmatch(r"-?\d+\.\d{1,2}", core):
        return number(core, "en-US")
    return number(raw, "de-DE")


def _same_number(expected, observed) -> bool:
    a, b = _num(expected), _num(observed)
    return a is not None and b is not None and abs(a - b) < Decimal("0.005")


def validate_review(expected: dict, observed: dict, skipped: list[str] | None = None) -> dict:
    """{"mismatches": [...], "missing": [...], "verified": [...], "blocking": bool}. Blocking = a critical field is wrong
    or missing (reference, offer type, totals, positions); optional breakdown fields only warn."""
    mismatches, missing, verified, blocking = [], [], [], False

    def check(label: str, ok: bool, critical: bool, detail: str = ""):
        nonlocal blocking
        if ok:
            verified.append(label)
        else:
            mismatches.append(f"{label}{': ' + detail if detail else ''}")
            blocking |= critical

    check("tender_reference", norm_id(observed.get("tender_reference")) == norm_id(expected["tender_reference"]), True,
          f"page shows {observed.get('tender_reference')!r}, expected {expected['tender_reference']!r}")
    offer = clean(observed.get("offer_type")).lower()
    wanted = "no_bid" if expected["decision"] == "no_bid" else "main"
    check("offer_type", offer == wanted, True, f"page shows {offer or 'nothing'}, expected {wanted}")
    if expected["decision"] == "no_bid":
        return {"mismatches": mismatches, "missing": missing, "verified": verified, "blocking": blocking}
    check("discount_percent", _same_number(expected["discount_percent"], observed.get("discount_percent")), True,
          f"page {observed.get('discount_percent')!r}, expected {expected['discount_percent']}")
    check("total_net", _same_number(expected["total_net"], observed.get("total_net")), True,
          f"page {observed.get('total_net')!r}, expected {expected['total_net']}")
    if observed.get("own_terms_declared") is True:
        check("own_terms", False, True, "own terms/AGB are declared; they must not be")
    if observed.get("documents_acknowledged") is False:
        check("documents_acknowledged", False, True, "tender documents are not acknowledged")

    seen = {}
    for p in observed.get("positions") or []:
        key = norm_id(p.get("position"))
        if key in seen:
            check(f"position {p.get('position')}", False, True, "appears twice")
        seen[key] = p
    expected_ids = {norm_id(p["position"]) for p in expected["positions"]}
    for key in seen:
        if key not in expected_ids:
            check(f"position {key}", False, True, "extra position on the page (remove it)")
    for p in expected["positions"]:
        got = seen.get(norm_id(p["position"]))
        if got is None:
            missing.append(f"position {p['position']}")
            blocking = True
            continue
        for field in CRITICAL + OPTIONAL:
            want, have = p.get(field), got.get(field)
            if want is None:
                continue
            if have in (None, ""):
                missing.append(f"position {p['position']} {field}")
                blocking |= field in CRITICAL
                continue
            ok = norm_id(have) == norm_id(want) if field == "material_number" else _same_number(want, have)
            check(f"position {p['position']} {field}", ok, field in CRITICAL, f"page {have!r}, expected {want}")
    for pid in skipped or []:
        if norm_id(pid) not in {norm_id(x) for x in observed.get("omitted_positions") or []} and norm_id(pid) in seen:
            check(f"position {pid}", False, False, "should be omitted")
    return {"mismatches": mismatches, "missing": missing, "verified": verified, "blocking": blocking}
