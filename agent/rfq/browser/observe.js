// Serializes one frame's DOM into compact text for the model, with refs for interactive elements.
// Rules (docs/05-browser-playbook.md): drop aria-hidden decoys and closed dialogs; keep tooltips (via
// aria-describedby), collapsed <details> content, canvas/role=img labels, ::before data-labels, <time datetime>;
// restore logical order (aria-posinset / CSS order); strip zero-width characters.
(opts) => {
  const prefix = opts.prefix || "e";
  const ZW = /[​-‍⁠﻿­]/g;
  const clean = (s) => (s || "").replace(ZW, "").replace(/ /g, " ").replace(/\s+/g, " ").trim();
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "HEAD", "META", "LINK"]);
  const BLOCK = new Set(["P", "DIV", "SECTION", "ARTICLE", "MAIN", "ASIDE", "HEADER", "FOOTER", "NAV", "UL", "OL",
    "LI", "DL", "DT", "DD", "FORM", "FIELDSET", "LEGEND", "TABLE", "THEAD", "TBODY", "TFOOT", "TR", "H1", "H2", "H3",
    "H4", "H5", "H6", "PRE", "BLOCKQUOTE", "DETAILS", "SUMMARY", "DIALOG", "CAPTION", "FIGURE", "BR", "HR", "LABEL"]);
  let n = 0;
  document.querySelectorAll("[data-agent-ref]").forEach((e) => e.removeAttribute("data-agent-ref"));

  const byId = (id) => (id ? document.getElementById(id) : null);
  const describedTexts = (el) => {
    const ids = (el.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
    return ids.map((id) => clean(byId(id)?.textContent)).filter(Boolean);
  };
  const referencedTooltip = new Set();
  document.querySelectorAll("[aria-describedby]").forEach((el) =>
    (el.getAttribute("aria-describedby") || "").split(/\s+/).forEach((id) => id && referencedTooltip.add(id)));

  const styleOf = (el) => { try { return getComputedStyle(el); } catch { return null; } };
  const cssHidden = (el) => {
    if (el.hidden) return true;
    const cs = styleOf(el);
    return !!cs && (cs.display === "none" || cs.visibility === "hidden");
  };
  const isInteractive = (el) => {
    const t = el.tagName;
    if (t === "A") return el.hasAttribute("href");
    if (["BUTTON", "SELECT", "TEXTAREA"].includes(t)) return true;
    if (t === "INPUT") return el.type !== "hidden";
    if (t === "SUMMARY") return true;
    const role = el.getAttribute("role");
    return ["button", "link", "menuitem", "tab", "checkbox", "radio", "option", "switch"].includes(role);
  };
  const ref = (el) => { const r = prefix + (++n); el.setAttribute("data-agent-ref", r); return r; };

  // Accessible name of a control.
  const labelText = (el) => {
    const parts = [];
    const walk = (node) => {
      if (node.nodeType === 3) { parts.push(node.textContent); return; }
      if (node.nodeType !== 1 || SKIP.has(node.tagName)) return;
      if (node.getAttribute("aria-hidden") === "true") return;
      if (node !== el && ["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(node.tagName)) return;
      if (node.getAttribute("data-label")) { parts.push(node.getAttribute("data-label")); return; }
      if (node.getAttribute("role") === "img" && node.getAttribute("aria-label")) { parts.push(node.getAttribute("aria-label")); return; }
      node.childNodes.forEach(walk);
    };
    walk(el);
    return clean(parts.join(" "));
  };
  const nameOf = (el) => {
    const aria = el.getAttribute("aria-label");
    if (aria) return clean(aria);
    const lb = el.getAttribute("aria-labelledby");
    if (lb) return clean(lb.split(/\s+/).map((id) => byId(id)?.textContent || "").join(" "));
    if (el.labels && el.labels.length) return labelText(el.labels[0]);
    if (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) return clean(el.placeholder || el.name || el.title || "");
    return clean(labelText(el) || el.title || el.value || "");
  };

  const controlLine = (el, hiddenCtx) => {
    const t = el.tagName;
    const r = ref(el);
    const flags = [];
    if (el.disabled || el.closest("[inert]") || el.closest("fieldset[disabled]")) flags.push("disabled");
    if (hiddenCtx) flags.push("hidden-until-hover/open");
    const info = describedTexts(el);
    let s;
    if (t === "A") {
      const href = el.getAttribute("href");
      s = `[${r}] link "${nameOf(el)}" -> ${href}` + (el.target ? ` (target=${el.target})` : "");
    } else if (t === "SELECT") {
      const opts = [...el.options].map((o) => (o.selected ? `*${clean(o.textContent)}*` : clean(o.textContent)) + (o.value !== clean(o.textContent) ? `{${o.value}}` : ""));
      s = `[${r}] select "${nameOf(el)}" options: ${opts.join(" | ")}`;
    } else if (t === "TEXTAREA") {
      s = `[${r}] textarea "${nameOf(el)}" value="${clean(el.value)}"`;
    } else if (t === "INPUT") {
      const ty = el.type;
      if (ty === "checkbox" || ty === "radio") s = `[${r}] ${ty} "${nameOf(el)}" ${el.checked ? "[x]" : "[ ]"}`;
      else if (ty === "range") s = `[${r}] slider "${nameOf(el)}" value=${el.value} min=${el.min || 0} max=${el.max || 100}`;
      else if (ty === "submit" || ty === "button") s = `[${r}] button "${clean(el.value) || nameOf(el)}"`;
      else s = `[${r}] input[${ty}] "${nameOf(el)}" value="${clean(el.value)}"`;
    } else if (t === "SUMMARY") {
      const d = el.parentElement;
      s = `[${r}] summary "${labelText(el)}" (${d && d.open ? "open" : "collapsed; content shown below"})`;
    } else {
      const role = el.getAttribute("role") || t.toLowerCase();
      s = `[${r}] ${role === "button" || t === "BUTTON" ? "button" : role} "${nameOf(el)}"`;
      if (el.getAttribute("aria-expanded")) s += ` expanded=${el.getAttribute("aria-expanded")}`;
    }
    if (flags.length) s += ` (${flags.join(", ")})`;
    if (info.length) s += ` [info: ${info.join(" / ")}]`;
    return s;
  };

  const out = [];
  let line = [];
  const flush = () => { const s = clean(line.join(" ")); if (s) out.push(s); line = []; };
  const emit = (s) => { if (s) line.push(s); };

  const orderedChildren = (el) => {
    const kids = [...el.childNodes];
    const keyed = kids.map((k, i) => {
      if (k.nodeType !== 1) return { k, i, key: 0 };
      const pos = k.getAttribute("aria-posinset");
      const ord = parseInt(styleOf(k)?.order || "0", 10) || 0;
      return { k, i, key: pos ? parseInt(pos, 10) : ord };
    });
    if (keyed.some((x) => x.key !== 0)) keyed.sort((a, b) => a.key - b.key || a.i - b.i);
    return keyed.map((x) => x.k);
  };

  const walk = (node, hiddenCtx) => {
    if (node.nodeType === 3) { if (!hiddenCtx) emit(node.textContent.replace(ZW, "")); return; }
    if (node.nodeType !== 1) return;
    const el = node;
    const t = el.tagName;
    if (SKIP.has(t)) return;
    if (el.getAttribute("aria-hidden") === "true") return;               // decoys and decoration
    if (t === "DIALOG" && !el.open) return;                               // closed popups
    const role = el.getAttribute("role");
    if (role === "tooltip" && el.id && referencedTooltip.has(el.id)) return; // shown as [info: …] on its trigger
    const hidden = hiddenCtx || cssHidden(el);
    // Content of collapsed <details> is not rendered but is real content: keep it.
    const inClosedDetails = !!el.closest("details:not([open])") && t !== "SUMMARY";
    const effHidden = hidden && !(inClosedDetails && !el.hidden && styleOf(el)?.display !== "none");
    const block = BLOCK.has(t);
    if (block) flush();
    if (t === "IFRAME" || t === "FRAME") {
      emit(`[iframe "${clean(el.title || el.name)}" src=${el.getAttribute("src") || ""}${el.hasAttribute("srcdoc") ? " (srcdoc)" : ""}] (content listed as its own frame)`);
      flush(); return;
    }
    if (/^H[1-6]$/.test(t) && !effHidden) emit("#".repeat(+t[1]));
    if (t === "DIALOG" || role === "dialog" || role === "alertdialog") {
      emit(`[DIALOG OPEN "${nameOf(el)}"]`); flush();
    }
    if (isInteractive(el)) {
      if (!effHidden || t === "A" || t === "BUTTON") { flush(); out.push(controlLine(el, effHidden)); }
      return;  // the control line carries its name; no need to walk inside
    }
    if (t === "LABEL" && el.querySelector("input,select,textarea") && !effHidden) {
      el.querySelectorAll("input,select,textarea").forEach((c) => { if (c.type !== "hidden") { flush(); out.push(controlLine(c, false)); } });
      flush(); return;  // the label text is already the controls' name
    }
    if (effHidden) {
      // Hidden subtree: only surface controls (menus that open on hover), never hidden text.
      el.querySelectorAll("a[href],button").forEach((c) => {
        if (c.closest("[aria-hidden='true']") || c.closest("dialog:not([open])")) return;
        flush(); out.push(controlLine(c, true));
      });
      return;
    }
    if (t === "CANVAS" || t === "IMG" || t === "SVG" || role === "img") {
      const label = clean(el.getAttribute("aria-label") || el.getAttribute("alt") || el.getAttribute("title") || (t === "CANVAS" ? el.textContent : ""));
      if (label) emit(`[${t === "IMG" || t === "CANVAS" ? t.toLowerCase() : "img"}: ${label}]`);
      else if (t === "CANVAS" || t === "IMG") {
        const box = el.getBoundingClientRect();
        const size = `${Math.round(box.width)}x${Math.round(box.height)} px`;
        if (box.width * box.height >= 2500) {        // real pictures, not icons: targetable by inspect_visual
          flush(); out.push(`[${ref(el)}] ${t === "CANVAS" ? "canvas without text: needs screenshot" : "image without text"} (${size})`);
        } else if (t === "IMG") emit("[image without text]");
      }
      if (block) flush();
      return;
    }
    if (el.getAttribute("data-label") && !el.textContent.trim()) emit(el.getAttribute("data-label"));
    if (t === "TIME" && el.getAttribute("datetime")) { emit(`${clean(el.textContent)} (${el.getAttribute("datetime")})`); return; }
    if (role === "tooltip") { emit(`[tooltip: ${clean(el.textContent)}]`); return; }
    if (t === "TR") {
      const cells = [...el.children].filter((c) => c.tagName === "TD" || c.tagName === "TH");
      const saved = out.length;
      const texts = cells.map((c) => {
        const before = out.length; line = [];
        orderedChildren(c).forEach((k) => walk(k, false));
        flush();
        const parts = out.splice(before);
        return parts.join(" ; ");
      });
      out.length = saved;
      out.push("| " + texts.join(" | ") + " |");
      return;
    }
    const extra = describedTexts(el);
    orderedChildren(el).forEach((k) => walk(k, false));
    if (extra.length) emit(`[info: ${extra.join(" / ")}]`);
    if (block) flush();
  };

  walk(document.body || document.documentElement, false);
  flush();
  const tz = (document.body?.innerText.match(/Zeit(?:zone|angaben)\s*:\s*([A-Za-z_\/]+|UTC)/) || [])[1];
  return { text: out.join("\n"), refs: n, title: document.title, timezone: tz || null };
}
