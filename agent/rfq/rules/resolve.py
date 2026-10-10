"""From extracted evidence to the bid: deterministic application of the brief's rules (docs/04-business-rules.md).

Input is the blackboard (raw extractions with source URLs) and the task context. Output is the answer (schema of the
run) plus a trace of every decision and the positions that could not be completed (to be skipped in the form).
Nothing here raises on missing data: it degrades to the best possible answer and says why in the trace.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal as D
from math import ceil

from .parse import cert, clean, day, integer, mail_address, norm_id, number, qa_number, revision_is_current, same_text, when

CENT = D("0.01")


def r2(value: D) -> D:
    return value.quantize(CENT, ROUND_HALF_UP)


@dataclass
class Context:
    tender_reference: str
    scenario: datetime
    director_localpart: str | None = None
    authorized: dict[str, str] = field(default_factory=dict)      # topic -> sender local part
    mailbox_domain: str | None = None
    searched_groups: set[str] = field(default_factory=set)        # catalogue groups whose ERP search is complete


@dataclass
class Result:
    answer: dict
    trace: list[str]
    skipped: list[str]                # positions we could not complete
    breakdown: dict                   # per position, unrounded components (for logs)


def largest_remainder(total: D, weights: dict[str, D]) -> dict[str, D]:
    """Distribute `total` in cents proportionally; floor first, remaining cents to the largest remainders,
    ties by ascending position id."""
    weights = {k: w for k, w in weights.items() if w > 0}
    if not weights or total <= 0:
        return {k: D(0) for k in weights}
    cents = int((total * 100).to_integral_value(ROUND_HALF_UP))
    wsum = sum(weights.values())
    raw = {k: D(cents) * w / wsum for k, w in weights.items()}
    base = {k: int(v.to_integral_value(ROUND_FLOOR)) for k, v in raw.items()}
    for k in sorted(weights, key=lambda k: (-(raw[k] - base[k]), k))[: cents - sum(base.values())]:
        base[k] += 1
    return {k: D(c) / 100 for k, c in base.items()}


def marginal_bonus(total: D, bands: list[dict]) -> D:
    credit = D(0)
    for band in bands:
        low = number(band.get("from_eur")) or D(0)
        high = number(band.get("to_eur"))
        rate = (number(band.get("rate_percent")) or D(0)) / 100
        top = total if high is None else min(total, high)
        if top > low:
            credit += (top - low) * rate
    return r2(credit)


def build(ctx: Context, bb: dict[str, list[dict]]) -> Result:
    trace: list[str] = []
    note = trace.append
    T = ctx.scenario
    ref_id = norm_id(ctx.tender_reference)

    # ---------------------------------------------------------------- positions & quantities
    positions: dict[str, dict] = {}
    for item in bb.get("positions", []):
        for p in item["data"].get("positions") or []:
            pid = clean(p.get("position"))
            if pid:
                positions.setdefault(pid, {}).update({k: v for k, v in p.items() if v not in (None, "")})
    note(f"positions: {list(positions)}")

    entries: dict[str, dict] = {}
    for item in bb.get("clarifications", []):
        for e in item["data"].get("entries") or []:
            if e.get("number"):
                entries[qa_number(e["number"])] = e
    def in_force(e: dict) -> bool:            # dated after the scenario date: no effect yet, nor its withdrawals
        at = when(e.get("datetime_utc"))
        return at is None or at <= T

    withdrawn = {qa_number(f.get("withdrawn_number")) for e in entries.values() if in_force(e) for f in e.get("facts") or []
                 if f.get("type") == "withdrawal" and f.get("withdrawn_number")}
    later = sorted({qa_number(f.get("withdrawn_number")) for e in entries.values() if not in_force(e)
                    for f in e.get("facts") or [] if f.get("type") == "withdrawal" and f.get("withdrawn_number")})
    if later:
        note(f"withdrawals dated after the scenario date (no effect): {later}")
    if withdrawn:
        note(f"withdrawn Q&A answers: {sorted(withdrawn)}")
    facts = []                                                   # (datetime, number, fact)
    for key, e in entries.items():
        at = when(e.get("datetime_utc"))
        if key in withdrawn:
            continue
        if at is not None and at > T:
            note(f"Q&A {key} is dated after the scenario date; ignored")
            continue
        for f in e.get("facts") or []:
            facts.append((at or datetime.min.replace(tzinfo=T.tzinfo), integer(key) or 0, f))
    facts.sort(key=lambda x: (x[0], x[1]))

    def latest(kind: str, pid: str | None = None, pred=lambda f: True) -> dict | None:
        found = None
        for _, _, f in facts:
            if f.get("type") == kind and (pid is None or same_text(f.get("position"), pid)) and pred(f):
                found = f
        return found

    # ---------------------------------------------------------------- specification
    revisions = [r for item in bb.get("spec_revisions", []) for r in item["data"].get("revisions") or []]
    current = [r for r in revisions if revision_is_current(r)]
    current_rev = norm_id(current[-1]["revision"]) if current else None
    if len({norm_id(r.get("revision")) for r in current}) > 1:
        note(f"WARNING several revisions look current: {[r.get('revision') for r in current]}; using {current_rev}")
    note(f"current specification revision: {current_rev}")
    requirements: dict[str, dict] = {}
    for item in bb.get("requirements", []):
        rev = norm_id(item["data"].get("revision"))
        if current_rev and rev and rev != current_rev:
            note(f"ignoring requirements of revision {rev} (not current)")
            continue
        for r in item["data"].get("requirements") or []:
            if r.get("ref"):
                requirements[norm_id(r["ref"])] = {**r, "revision": rev or current_rev}

    # ---------------------------------------------------------------- mails (authority and time)
    domain = ctx.mailbox_domain
    for item in bb.get("mail_list", []):
        address = clean(item["data"].get("mailbox_address"))
        if "@" in address:
            domain = address.split("@", 1)[1].lower()
    mails = []
    for item in bb.get("mail_message", []):
        m = item["data"]
        at = when(m.get("datetime_utc"))
        if m.get("is_draft_or_preview"):
            continue
        if norm_id(m.get("reference")) != ref_id:
            continue
        if at is None or at > T:
            note(f"mail '{clean(m.get('subject'))}' dated {at} is after the scenario date or undated; ignored")
            continue
        mails.append((at, m))
    mails.sort(key=lambda x: x[0])

    def authorized(topic: str, sender: str) -> bool:
        local = (ctx.director_localpart if topic == "discount" else ctx.authorized.get(topic)) or ""
        sender = mail_address(sender)
        if local and not sender.startswith(local.lower() + "@"):
            return False
        if domain and not sender.endswith("@" + domain):
            return False
        return True

    def topic_records(topic: str) -> list[dict]:
        return [m for _, m in mails if m.get("topic") == topic and authorized(topic, m.get("sender", ""))]

    def keyed(topic: str, field_name: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for m in topic_records(topic):                    # ascending by date: newer records replace older ones
            for k, v in ((m.get("data") or {}).get(field_name) or {}).items():
                out[norm_id(k)] = v
        return out

    clauses = keyed("clause_rules", "groups")
    reserves = keyed("tech_release", "entries")
    tariffs = keyed("packaging", "tariffs")
    commitments = keyed("delivery", "commitments")
    fx_rates = keyed("fx", "rates")
    pools = keyed("setup_pool", "pools")
    bonus_plans = keyed("project_bonus", "plans")
    indices = keyed("metal_index", "indices")
    note(f"mail keys: clauses={list(clauses)} reserves={list(reserves)} tariffs={list(tariffs)} "
         f"commitments={list(commitments)} fx={list(fx_rates)} pools={list(pools)} bonus={list(bonus_plans)} "
         f"indices={list(indices)} domain={domain}")

    # ---------------------------------------------------------------- materials
    materials: dict[str, dict] = {}
    for item in bb.get("materials", []):
        for m in item["data"].get("materials") or []:
            num = norm_id(m.get("material_number"))
            if not num:
                continue
            target = materials.setdefault(num, {"material_number": clean(m["material_number"]), "conditions": []})
            for k, v in m.items():
                if k == "conditions":
                    for c in v or []:
                        if c.get("scales") and c not in target["conditions"]:
                            target["conditions"].append(c)
                elif v not in (None, "", []):
                    target[k] = v

    def matches(mat: dict, req: dict, stroke: D | None) -> bool:
        """Exact match. stroke=None means 'nominal stroke unknown' (e.g. end reserve not found): stroke is then not
        compared here and the caller picks among the remaining candidates."""
        checks = [(number(mat.get("bore_mm")), number(req.get("bore_mm"))),
                  (number(mat.get("pressure_bar")), number(req.get("pressure_bar")))]
        if stroke is not None:
            checks.append((number(mat.get("stroke_mm")), stroke))
        if any(a is None or b is None or a != b for a, b in checks):
            return False
        if not same_text(mat.get("mounting"), req.get("mounting")):
            return False
        return cert(mat.get("certification")) == cert(req.get("certification"))

    # ---------------------------------------------------------------- per position
    resolved: dict[str, dict] = {}
    skipped: list[str] = []
    no_material: list[str] = []
    for pid, p in positions.items():
        qty, unit = number(p.get("quantity")), clean(p.get("unit"))
        change = latest("quantity_change", pid)
        if change:
            qty, unit = number(change.get("quantity")), clean(change.get("unit")) or unit
            note(f"{pid}: quantity from Q&A -> {qty} {unit}")
        req = requirements.get(norm_id(p.get("requirement_ref")))
        if req is None:
            note(f"{pid}: requirement {p.get('requirement_ref')} not found")
            skipped.append(pid)
            continue
        if unit and not unit.lower().startswith(("st", "stk", "stück", "pc", "piece")):
            per_set = integer(req.get("pieces_per_set"))
            if per_set:
                qty = qty * per_set
                note(f"{pid}: {unit} -> pieces x{per_set} = {qty}")
            else:
                note(f"{pid}: unit {unit} without pieces-per-set conversion")
        q = int(qty) if qty is not None else 0
        attrs = dict(req)
        for attribute in ("bore", "stroke", "pressure", "mounting", "certification"):
            amendment = latest("tech_amendment", None, lambda f, a=attribute: norm_id(f.get("revision")) == norm_id(req["revision"])
                               and norm_id(f.get("requirement")) == norm_id(req["ref"]) and f.get("attribute") == a)
            if amendment:
                field_name = {"bore": "bore_mm", "stroke": "stroke_mm", "pressure": "pressure_bar"}.get(attribute, attribute)
                attrs[field_name] = amendment.get("value")
                note(f"{pid}: amendment {attribute} = {amendment.get('value')}")
        stroke = number(attrs.get("stroke_mm"))
        usable_floor = None
        if attrs.get("usable_stroke") and stroke is not None:
            key = norm_id(attrs.get("end_reserve_key"))
            entry = reserves.get(key)
            if entry and (not entry.get("revision") or norm_id(entry["revision"]) == norm_id(req["revision"])):
                reserve = number(entry.get("reserve_mm_per_end")) or D(0)
                stroke = stroke + 2 * reserve
                note(f"{pid}: usable stroke + 2x{reserve} ({key}) = nominal {stroke}")
            else:
                note(f"{pid}: WARNING end reserve {key} for revision {req['revision']} not found; nominal stroke unknown")
                usable_floor, stroke = stroke, None
        group = norm_id(attrs.get("catalog_group"))
        candidates = [m for m in materials.values() if not group or not m.get("catalog_group") or norm_id(m.get("catalog_group")) == group]
        fitting = [m for m in candidates if matches(m, attrs, stroke)]
        if stroke is None and usable_floor is not None:
            # unknown reserve: the nominal stroke must exceed the usable one; prefer the closest such material
            longer = [m for m in fitting if (number(m.get("stroke_mm")) or D(0)) > usable_floor]
            ranked = sorted(longer or fitting, key=lambda m: (m.get("status_meaning") != "active", number(m.get("stroke_mm")) or D(0)))
            fitting = ranked[:1]
            uncertain = True
        else:
            uncertain = False
        active = [m for m in fitting if m.get("status_meaning") == "active"]
        note(f"{pid}: group {group} candidates={[m['material_number'] for m in candidates]} "
             f"fitting={[m['material_number'] for m in fitting]} active={[m['material_number'] for m in active]}")
        if not active:
            if fitting and all(m.get("status_meaning") is None for m in fitting):
                active = fitting
                note(f"{pid}: UNCERTAIN status unknown for {[m['material_number'] for m in fitting]}; best guess, not proof")
            elif candidates and not uncertain and group and group in ctx.searched_groups:
                no_material.append(pid)      # proven: the complete search found no active matching material
                continue
            elif candidates:
                note(f"{pid}: no fitting active material among {[m['material_number'] for m in candidates]}, but the "
                     f"search of group {group} is not proven complete: position skipped, no no_bid")
                skipped.append(pid)
                continue
            else:
                skipped.append(pid)
                continue
        material = sorted(active, key=lambda m: (not m.get("conditions"), m["material_number"]))[0]
        resolved[pid] = {"q": q, "material": material, "req": attrs}

    # A position without any active matching material (and evidence that we looked) means no bid.
    if no_material:
        note(f"no active matching material for {no_material}: no_bid")
        answer = {"tender_reference": ctx.tender_reference, "decision": "no_bid", "currency": "EUR",
                  "discount_percent": 0, "positions": [], "total_net": 0}
        return Result(answer, trace, [], {})

    # ---------------------------------------------------------------- prices
    def condition_price(m: dict, q: int, pid: str) -> D | None:
        valid = []
        for c in m.get("conditions") or []:
            start, end = when(c.get("valid_from")), when(c.get("valid_to"))
            if start and start > T and day(c.get("valid_from")) != T.date():
                continue
            if end and end < T and day(c.get("valid_to")) != T.date():
                continue
            valid.append(c)
        if not valid:
            note(f"{pid}: no price condition valid on {T.date()}")
            return None
        c = valid[-1]
        fmt = c.get("number_format")
        scales = sorted(((number(s.get("from_qty"), fmt), number(s.get("price"), fmt)) for s in c.get("scales") or []),
                        key=lambda s: s[0] or 0)
        usable = [s for s in scales if s[0] is not None and s[1] is not None and s[0] <= q] or scales[:1]
        if not usable:
            return None
        price = usable[-1][1] / (number(c.get("price_unit_qty"), fmt) or D(1))
        currency = clean(c.get("currency")).upper() or "EUR"
        if currency != "EUR":
            rate = fx_rates.get(norm_id(c.get("fx_key")))
            eur = number((rate or {}).get("eur_per_unit"))
            if eur is None:
                note(f"{pid}: no FX rate for {c.get('fx_key')} ({currency}); price left in {currency}")
                eur = D(1)
            price *= eur
        base = r2(price)
        note(f"{pid}: scale from {usable[-1][0]} price {usable[-1][1]} / unit {c.get('price_unit_qty')} {currency} -> base {base} EUR")
        return base

    for pid, r in resolved.items():
        r["base"] = condition_price(r["material"], r["q"], pid)

    # discount (order level, possibly conditional)
    approvals = [m for m in topic_records("discount") if (m.get("data") or {}).get("is_approval")]
    discount = D(0)
    if approvals:
        data = approvals[-1]["data"]
        discount = number(data.get("percent")) or D(0)
        threshold = number(data.get("min_order_value_eur"))
        if threshold is not None:
            order_value = r2(sum((r["q"] * r["base"] for r in resolved.values() if r["base"] is not None), D(0)))
            if order_value < threshold:
                note(f"discount condition failed: {order_value} < {threshold}")
                discount = D(0)
            else:
                note(f"discount condition met: {order_value} >= {threshold}")
        note(f"discount {discount} % from '{clean(approvals[-1].get('subject'))}'")
    else:
        note("no discount approval found: 0 %")
    d = discount / 100

    # setup pools and project bonus
    pool_members: dict[str, list[str]] = defaultdict(list)
    bonus_members: dict[str, list[str]] = defaultdict(list)
    assignments = {}
    for pid in resolved:
        a: dict = {}
        for _, _, f in facts:      # merge assignment facts in date order; later non-empty keys win
            if f.get("type") == "assignment" and same_text(f.get("position"), pid):
                a.update({k: v for k, v in f.items() if v not in (None, "")})
        assignments[pid] = a
        if a.get("setup_pool_key"):
            pool_members[norm_id(a["setup_pool_key"])].append(pid)
        if a.get("bonus_plan_key"):
            bonus_members[norm_id(a["bonus_plan_key"])].append(pid)
    pool_share: dict[str, D] = defaultdict(D)
    for key, members in pool_members.items():
        amount = number((pools.get(key) or {}).get("amount_eur"))
        if amount is None:
            note(f"setup pool {key}: amount not found")
            continue
        weights = {p: D(resolved[p]["q"]) * (number(resolved[p]["material"].get("allocation_weight_g")) or D(0)) for p in members}
        for p, share in largest_remainder(amount, weights).items():
            pool_share[p] += share
        note(f"setup pool {key} {amount} -> {dict(pool_share)}")
    bonus_share: dict[str, D] = defaultdict(D)
    for key, members in bonus_members.items():
        plan = bonus_plans.get(key)
        eligible = {p: resolved[p]["q"] * resolved[p]["base"] for p in members if resolved[p]["base"] is not None}
        if not plan or not eligible:
            note(f"bonus plan {key}: not found or no eligible prices")
            continue
        credit = marginal_bonus(sum(eligible.values()), plan.get("bands") or [])
        for p, share in largest_remainder(credit, eligible).items():
            bonus_share[p] += share
        note(f"bonus plan {key}: base {sum(eligible.values())} credit {credit}")

    pooled: dict[str, int] = defaultdict(int)
    for r in resolved.values():
        pooled[norm_id(r["material"]["material_number"])] += r["q"]

    out_positions, breakdown, total = [], {}, D(0)
    for pid, r in resolved.items():
        m, q, base = r["material"], r["q"], r["base"]
        if base is None or q <= 0:
            skipped.append(pid)
            continue
        mass = (number(m.get("surcharge_mass_g")) or D(0)) / 1000
        index = _index_rate(indices.get(norm_id(m.get("price_index_key"))), T) if m.get("price_index_key") else None
        if m.get("price_index_key") and index is None:
            note(f"{pid}: metal index {m.get('price_index_key')} not found")
        mass_surcharge = mass * (index or D(0))
        group = latest("condition_group", pid)
        scope = clauses.get(norm_id(group.get("group")), "base_only") if group else "base_only"
        setup_fact = latest("setup_cost", pid)
        setup = (number(setup_fact.get("amount_eur")) or D(0)) if setup_fact else D(0)
        setup += pool_share.get(pid, D(0))
        a = assignments[pid]
        tariff = number((tariffs.get(norm_id(a.get("packaging_key"))) or {}).get("eur_per_carton")) if a.get("packaging_key") else None
        carton = integer(m.get("pieces_per_carton"))
        packaging = D(ceil(q / carton)) * tariff if tariff is not None and carton else D(0)
        bonus = bonus_share.get(pid, D(0))
        disc_base = base * (1 - d) if scope in ("base_only", "base_and_surcharge") else base
        disc_mass = mass_surcharge * (1 - d) if scope == "base_and_surcharge" else mass_surcharge
        unit_price = r2(disc_base + disc_mass + (setup + packaging - bonus) / q)
        lead = _lead_time(m, pooled[norm_id(m["material_number"])], commitments.get(norm_id(a.get("delivery_key"))) if a.get("delivery_key") else None)
        total += q * unit_price
        out_positions.append({
            "position": pid, "material_number": m["material_number"], "quantity": q,
            "unit_price": float(unit_price), "lead_time_weeks": lead,
            "base_unit_price": float(base), "surcharge_per_unit": float(r2(mass_surcharge + setup / q)),
            "packaging_per_unit": float(r2(packaging / q)), "rebate_total": float(r2(bonus)),
        })
        breakdown[pid] = {"scope": scope, "index": index, "mass_surcharge": mass_surcharge, "setup": setup,
                          "packaging": packaging, "bonus": bonus, "unit_price": unit_price,
                          "filters": {k: m.get(k) for k in ("bore_mm", "stroke_mm", "pressure_bar", "mounting", "certification")}}
        note(f"{pid}: {m['material_number']} q={q} base={base} scope={scope} mass={mass_surcharge} setup={setup} "
             f"pack={packaging} bonus={bonus} -> {unit_price}, lead {lead}")
    answer = {"tender_reference": ctx.tender_reference, "decision": "bid", "currency": "EUR",
              "discount_percent": float(discount), "positions": out_positions, "total_net": float(r2(total))}
    return Result(answer, trace, sorted(set(skipped)), breakdown)


def _index_rate(entries, T: datetime) -> D | None:
    if not entries:
        return None
    if isinstance(entries, dict):
        entries = [entries]
    for e in entries:
        start, end = day(e.get("valid_from")), day(e.get("valid_to"))
        if (start is None or start <= T.date()) and (end is None or T.date() <= end):
            if start or end:
                return number(e.get("eur_per_kg"))
    return number(entries[-1].get("eur_per_kg"))


def _lead_time(m: dict, pooled_qty: int, commitment: dict | None) -> int:
    if commitment and norm_id(commitment.get("material")) == norm_id(m["material_number"]):
        limit = number(commitment.get("max_total_qty"))
        if limit is not None and pooled_qty <= limit:
            return integer(commitment.get("weeks")) or 0
    stock = number(m.get("unrestricted_stock"))
    if stock is not None and pooled_qty <= stock:
        return integer(m.get("lead_time_stock_weeks")) or 0
    return integer(m.get("lead_time_production_weeks")) or integer(m.get("lead_time_stock_weeks")) or 0
