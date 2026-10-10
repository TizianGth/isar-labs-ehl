// Generic page guards (no site-specific selectors). op: state | consent | close_overlays | unlock_gates
(args) => {
  const op = args.op;
  const txt = (e) => (e?.innerText || e?.textContent || "").replace(/[​-‍⁠﻿]/g, "").trim();
  const name = (e) => ((e.getAttribute("aria-label") || "") + " " + txt(e) + " " + (e.value || "")).toLowerCase();
  const visible = (e) => { const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none"; };
  const body = txt(document.body).slice(0, 4000);
  const clickables = (root) => [...root.querySelectorAll("button, a[href], input[type=submit], input[type=button], [role=button]")];
  const BAD = /akzeptieren und fortfahren|umfrage|bewert|feedback|survey|rate us|teilnehmen|zur befragung/;

  if (op === "state") {
    const hasPassword = [...document.querySelectorAll("input[type=password]")].some(visible);
    const errorPage = document.querySelectorAll("a,button,input,select").length < 25 &&
      /vorübergehend nicht verfügbar|service unavailable|bad gateway|gateway timeout|interner fehler|internal server error|fehler 50\d|error 50\d/i.test(body);
    let retry = null;
    if (errorPage) {
      const a = [...document.querySelectorAll("a[href]")].find((x) => /erneut|nochmal|retry|wiederholen/i.test(txt(x)));
      retry = a ? a.getAttribute("href") : null;
    }
    const consent = !hasPassword && /cookie/i.test(body) &&
      clickables(document).some((b) => visible(b) && /cookie|akzeptier|zustimm|einverstanden|accept/.test(name(b)));
    const pending = [...document.querySelectorAll("dialog:not([open])")].length > 0;
    return { login: hasPassword, error_page: errorPage, retry_href: retry, consent, pending_dialogs: pending };
  }

  if (op === "consent") {
    const buttons = clickables(document).filter(visible);
    const pick = buttons.find((b) => /notwendig|necessary|essential|erforderlich/.test(name(b)) && /akzept|accept|nur|only|zulassen|allow/.test(name(b)))
      || buttons.find((b) => /notwendig|necessary/.test(name(b)))
      || buttons.find((b) => /akzeptier|accept|zustimm|einverstanden/.test(name(b)) && !BAD.test(name(b)));
    if (pick) { pick.click(); return true; }
    return false;
  }

  if (op === "close_overlays") {
    let closed = 0;
    const overlays = [...document.querySelectorAll("dialog[open], [role=dialog][aria-modal=true], [role=alertdialog]")]
      .filter((d) => d.tagName === "DIALOG" || visible(d));
    for (const d of overlays) {
      const buttons = clickables(d);
      const close = buttons.find((b) => /schlie|close|^\s*[×✕x]\s*$|später|nicht jetzt|nein danke|no thanks|abbrechen|ausblenden/.test(name(b).trim()) && !BAD.test(name(b)));
      if (close) { close.click(); closed++; continue; }
      if (d.tagName === "DIALOG" && typeof d.close === "function") { d.close(); closed++; }
    }
    return closed;
  }

  if (op === "unlock_gates") {
    // "drag to unlock" sliders and "I am not a robot" boxes that keep a fieldset disabled/inert.
    let done = 0;
    for (const r of document.querySelectorAll("input[type=range]")) {
      const label = ((r.getAttribute("aria-label") || "") + " " + txt(r.closest("label") || r.parentElement)).toLowerCase();
      if (!/entsperr|unlock|zieh|slide|schieb/.test(label)) continue;
      const max = r.max || "100";
      if (r.value !== max) {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
        setter.call(r, max);
        r.dispatchEvent(new Event("input", { bubbles: true }));
        r.dispatchEvent(new Event("change", { bubbles: true }));
        done++;
      }
    }
    for (const c of document.querySelectorAll("input[type=checkbox]")) {
      const label = txt(c.closest("label") || c.parentElement).toLowerCase();
      if (/roboter|robot|kein bot|human|mensch/.test(label) && !c.checked) { c.click(); done++; }
    }
    return done;
  }
  return null;
}
