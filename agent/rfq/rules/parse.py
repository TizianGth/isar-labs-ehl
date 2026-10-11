"""Parsing of raw values as shown on the websites: numbers with locale tags, dates, units, identifiers."""
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

ZERO_WIDTH = re.compile(r"[​-‍⁠﻿­]")


def clean(text) -> str:
    return re.sub(r"\s+", " ", ZERO_WIDTH.sub("", str(text or "")).replace(" ", " ")).strip()


def mail_address(sender) -> str:
    """'Anna Berg <a.berg@firma.example>' -> 'a.berg@firma.example' (lower case)."""
    sender = clean(sender).lower()
    return sender.split("<")[-1].rstrip(">").strip() if "<" in sender else sender


def norm_id(text) -> str:
    """Identifier for comparisons: no zero-width chars, no spaces, upper case."""
    return re.sub(r"\s+", "", clean(text)).upper()


def number(raw, locale: str | None = None) -> Decimal | None:
    """'4.321,09 (de-DE)' -> 4321.09 ; '512,345 (en-US)' -> 512345 ; '7.85' -> 7.85 ; '5 %' -> 5."""
    if raw is None:
        return None
    if isinstance(raw, (int, float, Decimal)):
        return Decimal(str(raw))
    text = clean(raw)
    tag = re.search(r"\((de-DE|en-US|en-GB|de-AT|de-CH|fr-FR)\)", text)
    locale = (tag.group(1) if tag else locale) or ""
    text = re.sub(r"\(.*?\)", "", text)
    match = re.search(r"-?[\d.,'’ ]*\d", text)
    if not match:
        return None
    digits = match.group(0).replace(" ", "").replace("'", "").replace("’", "")
    if locale.startswith("de") or locale.startswith("fr"):
        digits = digits.replace(".", "").replace(",", ".")
    elif locale.startswith("en"):
        digits = digits.replace(",", "")
    elif "," in digits and "." in digits:                      # both: the last one is the decimal separator
        if digits.rfind(",") > digits.rfind("."):
            digits = digits.replace(".", "").replace(",", ".")
        else:
            digits = digits.replace(",", "")
    elif "," in digits:                  # only commas: thousands groups ("1,234,567" / "512,345") or a German decimal
        parts = digits.split(",")
        thousands = len(parts) > 2 or len(parts[-1]) == 3
        digits = digits.replace(",", "") if thousands else digits.replace(",", ".")
    elif digits.count(".") > 1:          # "1.234.567": German thousands groups
        digits = digits.replace(".", "")
    try:
        return Decimal(digits)
    except InvalidOperation:
        return None


_GROUPED = re.compile(r"-?[1-9]\d{0,2}(?:([.,'’ ])\d{3})(?:\1\d{3})*")


def count(raw, locale: str | None = None) -> Decimal | None:
    """A whole quantity (pieces, stock, cartons, grams, scale quantities). A separator followed by groups of three
    digits is a thousands separator in either convention ('1.250' and '1,250' are 1250), never three decimals; German
    pages often show '1.250 Stück' without any locale tag. Everything else is read like number()."""
    if raw is None or isinstance(raw, (int, float, Decimal)):
        return number(raw, locale)
    text = re.sub(r"\(.*?\)", "", clean(raw))
    match = re.search(r"-?\d[\d.,'’ ]*", text)
    if match:
        token = match.group(0).rstrip(".,'’ ")
        if _GROUPED.fullmatch(token):
            return Decimal(re.sub(r"[.,'’ ]", "", token))
    return number(raw, locale)


def integer(raw) -> int | None:
    value = count(raw)
    return int(value) if value is not None else None


def when(raw) -> datetime | None:
    """ISO 8601 (with Z/offset or date only) -> aware UTC datetime. Date-only values become 00:00 UTC."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    text = clean(raw)
    if not text or text.lower() == "null":
        return None
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    match = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?:,?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?)?", text)
    if match:
        d, m, y, hh, mm, ss = match.groups()
        return datetime(int(y), int(m), int(d), int(hh or 0), int(mm or 0), int(ss or 0), tzinfo=timezone.utc)
    return None


def day(raw) -> date | None:
    value = when(raw)
    return value.date() if value else None


def same_text(a, b) -> bool:
    return clean(a).casefold() == clean(b).casefold()


NO_CERT = {"", "NONE", "KEINE", "KEINEGEFORDERT", "OHNE", "OHNEZERTIFIZIERUNG", "NULL", "-", "—", "N/A"}


def cert(raw) -> str:
    value = norm_id(raw)
    return "" if value in NO_CERT else value


_NOT_CURRENT = re.compile(r"\b(un|nicht\s*)g[üu]ltig|\binvalid|ersetzt|superseded|veraltet|obsolet|zur[üu]ckgezogen|"
                          r"withdrawn|archiv|entwurf|draft|vorschau|preview|outdated|replaced", re.I)
_CURRENT = re.compile(r"(?<![a-zäöü])(g[üu]ltig|aktuell|current|valid|freigegeben|maßgeblich|massgeblich|in kraft)\b", re.I)


def revision_is_current(revision: dict) -> bool:
    """A revision counts as current only if it is marked so: an explicit is_current wins; otherwise the status text
    must say current/valid and must not say invalid/superseded/draft (substring tricks like 'ungültig' do not count)."""
    flag = revision.get("is_current")
    if flag is False:
        return False
    status = clean(revision.get("status_text"))
    if status and _NOT_CURRENT.search(status):
        return False
    return flag is True or bool(status and _CURRENT.search(status))
