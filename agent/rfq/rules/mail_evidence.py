"""Shared mail eligibility and keyed records for research checks and pricing."""
from decimal import InvalidOperation

from .parse import clean, mail_address, norm_id, number, when


def mailbox_domain(bb, fallback=None):
    domain = fallback
    for item in bb.get("mail_list", []):
        address = clean(item["data"].get("mailbox_address"))
        if "@" in address:
            domain = address.split("@", 1)[1].lower()
    return domain


def mail_problem(mail, reference, scenario, director, authorized, domain):
    if norm_id(mail.get("reference")) != norm_id(reference):
        return "other reference"
    if mail.get("is_draft_or_preview"):
        return "draft/preview"
    at = when(mail.get("datetime_utc"))
    if scenario is not None and (at is None or at > scenario):
        return "after the scenario date" if at else "no readable date"
    local = (director if mail.get("topic") == "discount" else authorized.get(mail.get("topic"))) or ""
    sender = mail_address(mail.get("sender"))
    if (local and not sender.startswith(local.lower() + "@")) or (domain and not sender.endswith("@" + domain)):
        return "not from the authorized sender"
    return None


def eligible_mails(bb, reference, scenario, director, authorized, domain=None):
    domain = mailbox_domain(bb, domain)
    records = [i["data"] for i in bb.get("mail_message", [])
               if mail_problem(i["data"], reference, scenario, director, authorized, domain) is None]
    return sorted(records, key=lambda m: when(m.get("datetime_utc")).timestamp()
                  if when(m.get("datetime_utc")) else float("-inf"))


def keyed_records(mails, topic, field):
    """Newest eligible record per key, including a newer incomplete record."""
    records = {}
    for mail in mails:
        if mail.get("topic") != topic:
            continue
        data = mail.get("data")
        values = data.get(field) if isinstance(data, dict) else None
        if isinstance(values, dict):
            records.update({norm_id(k): v for k, v in values.items()})
    return records


def amount(value):
    if isinstance(value, bool):
        return None
    try:
        return number(value)
    except (InvalidOperation, TypeError, ValueError):
        return None


def nonnegative(value):
    parsed = amount(value)
    return parsed is not None and parsed.is_finite() and parsed >= 0


def valid_bonus_plan(plan):
    bands = plan.get("bands") if isinstance(plan, dict) else None
    if not isinstance(bands, list) or not bands:
        return False
    ranges = []
    for band in bands:
        if not isinstance(band, dict) or "to_eur" not in band:
            return False
        low, high, rate = (amount(band.get(k)) for k in ("from_eur", "to_eur", "rate_percent"))
        if not nonnegative(low) or not nonnegative(rate) or rate > 100:
            return False
        if band["to_eur"] is not None and (not nonnegative(high) or high <= low):
            return False
        ranges.append((low, high))
    ranges.sort(key=lambda r: r[0])
    return all(previous[1] is not None and previous[1] <= current[0]
               for previous, current in zip(ranges, ranges[1:]))
