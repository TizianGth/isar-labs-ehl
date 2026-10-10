"""Optional, model-selected form bindings, retained only by one submission explorer.

Plans contain semantic field bindings, never bid values or reusable browser refs. A changed form falls back to
the ordinary explorer before any field is touched. Dependent filters remain handled by the existing batch tool.
"""
import re
from decimal import Decimal

from ..rules.parse import norm_id

FIELDS = {"material_number", "quantity", "unit_price", "base_unit_price", "surcharge_per_unit",
          "packaging_per_unit", "rebate_total", "lead_time_weeks"}
UNSAFE = re.compile(r"(?:tan|sign|signature|signing|binding|verbindlich|abgeben|abschicken|confirm|bestätigen|submit|send|senden|einreichen|übermitteln)", re.I)
DESCRIBE = """e => {
    const label = x => { const l = x.labels && x.labels[0];
        const t = l ? [...l.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ') : '';
        return (t || x.getAttribute('aria-label') || x.getAttribute('placeholder') ||
            (x.matches('button') ? x.innerText : '')).trim(); };
    const visible = x => x.getClientRects().length && getComputedStyle(x).visibility !== 'hidden';
    const describe = x => ({tag:x.tagName, type:x.type || '', name:x.name || '', id:x.id || '', label:label(x),
        disabled:!!x.disabled, readonly:!!x.readOnly, required:!!x.required,
        min:x.min || '', max:x.max || '', pattern:x.pattern || '',
        options:x.tagName === 'SELECT' ? [...x.options].map(o => [o.value,o.textContent.trim(),o.disabled]) : []});
    const f = e.form || e.closest('form');
    return {field:describe(e), ref:e.getAttribute('data-agent-ref'),
        lang:e.getAttribute('lang') || (f && f.getAttribute('lang')) || document.documentElement.lang,
        form:f ? [...f.querySelectorAll('input,select,textarea,button')].filter(visible).map(x =>
            ({...describe(x),ref:x.getAttribute('data-agent-ref')})) : null};
}"""


def _shape(controls):
    return [{k: v for k, v in c.items() if k != "ref"} for c in controls or []]


def _value(value, descriptor):
    if descriptor["field"]["tag"] == "SELECT" or isinstance(value, str):
        return str(value)
    number = Decimal(str(value))
    if descriptor["field"]["type"] == "number":
        return format(number, "f")
    lang = str(descriptor.get("lang") or "").lower()
    if lang.startswith("de"):
        return format(number, "f").replace(".", ",")
    if lang.startswith("en"):
        return format(number, "f")
    raise ValueError("unknown field number locale; use ordinary fill_fields")


class FormPlans:
    def __init__(self):
        self.plans = {}

    async def _position(self, site, ref, position):
        _, marker = await site._element(ref)
        text = await marker.evaluate("e => e.matches('input') ? e.value : e.innerText")
        text = " ".join(text.split())
        if len(text) > 120 or not re.search(rf"(?<![\w-]){re.escape(str(position))}(?![\w-])", text, re.I):
            raise ValueError("the selected position marker does not identify this position")

    async def remember(self, site, plan_id, fields, position, position_ref, then_click=None):
        async with site.session.operation():
            return await self._remember(site, plan_id, fields, position, position_ref, then_click)

    async def _remember(self, site, plan_id, fields, position, position_ref, then_click=None):
        if then_click:
            raise ValueError("form plans only fill fields; transitions require the ordinary explorer")
        await self._position(site, position_ref, position)
        bindings, shape, button = [], None, None
        if not fields or len(fields) > 20:
            raise ValueError("select between one and twenty fields")
        for f in fields:
            key = str(f.get("value_key", ""))
            if key not in FIELDS or f.get("apply"):
                raise ValueError("use computed position fields; dependent filters need fill_fields")
            _, el = await site._element(str(f["ref"]))
            desc = await el.evaluate(DESCRIBE)
            if not desc["form"] or desc["field"]["tag"] not in ("INPUT", "SELECT", "TEXTAREA"):
                raise ValueError("a plan needs controls inside one form")
            if UNSAFE.search(" ".join(str(v) for v in desc["field"].values())):
                raise ValueError("TAN and signing controls cannot be planned")
            current = _shape(desc["form"])
            if shape is not None and shape != current:
                raise ValueError("all planned controls must have the same form fingerprint")
            shape = current
            if sum(c == desc["field"] for c in shape) != 1:
                raise ValueError("ambiguous semantic field")
            bindings.append((key, desc["field"]))
        if len({str(f["ref"]) for f in fields}) != len(fields):
            raise ValueError("a field was selected twice")
        self.plans[str(plan_id)] = (bindings, shape, button, site.session.epoch)
        return "Remembered semantic form plan for this run; values and browser refs were not retained."

    async def apply(self, site, plan_id, answer, position, position_ref):
        async with site.session.operation():
            return await self._apply(site, plan_id, answer, position, position_ref)

    async def _apply(self, site, plan_id, answer, position, position_ref):
        plan = self.plans.get(str(plan_id))
        if plan is None:
            raise ValueError("no such plan in this run")
        bindings, shape, button, epoch = plan
        if epoch != site.session.epoch:
            raise ValueError("session changed; learn the form again")
        rows = [p for p in answer.get("positions", []) if norm_id(p.get("position")) == norm_id(position)]
        if len(rows) != 1:
            raise ValueError("position is not uniquely present in our computed bid")
        await self._position(site, position_ref, rows[0]["position"])
        # Find the live form via an explicitly selected marker, never an old ref or guessed URL.
        _, marker = await site._element(position_ref)
        desc = await marker.evaluate(DESCRIBE)
        if _shape(desc["form"]) != shape:
            raise ValueError("form fingerprint/options changed; use ordinary fill_fields")
        fields = []
        for key, identity in bindings:
            candidates = [c for c in desc["form"] if {k:v for k,v in c.items() if k != "ref"} == identity]
            if len(candidates) != 1 or not candidates[0]["ref"] or rows[0].get(key) is None:
                raise ValueError("field or computed value is ambiguous/missing")
            live = candidates[0]
            fields.append({"ref": live["ref"], "value": _value(rows[0][key], {"field": live, "lang": desc["lang"]})})
        if button:
            raise ValueError("transition-bearing plans are not supported")
        return await site.act("fill_fields", {"fields": fields})
