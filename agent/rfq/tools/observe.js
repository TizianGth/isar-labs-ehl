// Page serializer: turns one frame's DOM into compact, trap-resistant text for the model.
// Called with {prefix, scope, includeHidden}. Interactive elements get a stable data-agent-ref.
// - hidden decoys (hidden, aria-hidden=true, display:none, visibility:hidden) are dropped
// - tooltips (aria-describedby targets, role=tooltip, title) are attached to their owner even when hidden
// - closed <details> content is shown and marked
// - canvas / role=img / img / output use their aria-label (clean value), not the drawn or zero-width text
// - CSS ::before/::after content (e.g. attr(data-label)), <time datetime>, aria-posinset / flex order are honoured
(args) => {
  const prefix = (args && args.prefix) || "";
  const ZW = /[​-‍⁠﻿­]/g;
  const norm = (s) => (s || "").replace(ZW, "").replace(/ /g, " ").replace(/\s+/g, " ").trim();
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "HEAD", "META", "LINK"]);
  const BLOCK = new Set(["P", "DIV", "SECTION", "ARTICLE", "MAIN", "HEADER", "FOOTER", "NAV", "ASIDE", "UL", "OL",
    "LI", "DL", "DT", "DD", "FORM", "FIELDSET", "LEGEND", "H1", "H2", "H3", "H4", "H5", "H6", "TABLE", "PRE",
    "BLOCKQUOTE", "FIGURE", "FIGCAPTION", "DETAILS", "SUMMARY", "DIALOG", "BR", "HR", "ADDRESS", "LABEL"]);
  const IMGROLE = new Set(["img", "image", "graphics-symbol"]);
  const INTERACTIVE_ROLES = new Set(["button", "link", "tab", "menuitem", "menuitemcheckbox", "menuitemradio",
    "checkbox", "radio", "switch", "option", "combobox", "slider", "spinbutton", "textbox", "searchbox", "treeitem"]);

  if (window.__agentRefSeq === undefined) window.__agentRefSeq = 0;
  const describedBy = new Set();
  document.querySelectorAll("[aria-describedby]").forEach((el) =>
    el.getAttribute("aria-describedby").split(/\s+/).forEach((id) => id && describedBy.add(id)));

  const style = (el) => { try { return getComputedStyle(el); } catch (e) { return null; } };
  const isDecoy = (el) => {
    if (el.hasAttribute("hidden") && el.tagName !== "DETAILS") return true;
    if (el.getAttribute("aria-hidden") === "true") return true;
    const s = style(el);
    if (!s) return false;
    return s.display === "none" || s.visibility === "hidden" || s.visibility === "collapse" || s.opacity === "0";
  };
  const isTooltip = (el) => el.getAttribute("role") === "tooltip" || (el.id && describedBy.has(el.id));
  const textOf = (el) => norm(el.innerText !== undefined && el.innerText !== "" ? el.innerText : el.textContent);
  // tooltip text even when display:none (innerText would be empty) — but never aria-hidden decoys
  const tipText = (el) => (el.getAttribute("aria-hidden") === "true" ? "" : norm(el.textContent));

  // visible text of a subtree: decoys dropped, image-like nodes replaced by their aria-label
  const richText = (el) => {
    const parts = [];
    const rec = (n) => {
      if (n.nodeType === 3) { parts.push(n.data); return; }
      if (n.nodeType !== 1 || SKIP.has(n.tagName)) return;
      if (n.getAttribute("role") === "tooltip" || isDecoy(n)) return;
      const r = n.getAttribute("role");
      if (n.tagName === "CANVAS" || (r && IMGROLE.has(r)) || n.tagName === "IMG") {
        parts.push(" " + (n.getAttribute("aria-label") || n.getAttribute("alt") || "") + " "); return;
      }
      const b = pseudo(n, "::before"); if (b) parts.push(" " + b + " ");
      n.childNodes.forEach(rec);
    };
    rec(el);
    return norm(parts.join(""));
  };

  const labelOf = (el) => {
    const al = el.getAttribute("aria-label");
    if (al && norm(al)) return norm(al);
    const lb = el.getAttribute("aria-labelledby");
    if (lb) {
      const t = lb.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean).map((e) => norm(e.textContent)).join(" ");
      if (t) return t;
    }
    if (el.labels && el.labels.length) {
      const t = [...el.labels].map((l) => {
        const c = l.cloneNode(true);
        c.querySelectorAll("input,select,textarea,script,style").forEach((x) => x.remove());
        c.querySelectorAll("[aria-label]").forEach((x) => { x.textContent = " " + x.getAttribute("aria-label") + " "; });
        return norm(c.textContent);
      }).join(" ");
      if (t) return t;
    }
    if (el.tagName === "INPUT" && ["submit", "button", "reset"].includes(el.type)) return norm(el.value);
    if (el.tagName === "IMG") return norm(el.getAttribute("alt"));
    const inner = ["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName) ? "" : richText(el);
    return inner || norm(el.getAttribute("placeholder")) || norm(el.getAttribute("title")) || norm(el.getAttribute("name")) || "";
  };

  const extras = (el) => {
    const out = [];
    const db = el.getAttribute("aria-describedby");
    if (db) {
      const t = db.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean).map(tipText).filter(Boolean).join(" · ");
      if (t) out.push("[Tooltip: " + t + "]");
    }
    const desc = norm(el.getAttribute("aria-description"));
    if (desc) out.push("[Tooltip: " + desc + "]");
    const title = norm(el.getAttribute("title"));
    if (title && title !== labelOf(el)) out.push("[Titel: " + title + "]");
    return out.join(" ");
  };

  const pseudo = (el, which) => {
    const s = (() => { try { return getComputedStyle(el, which); } catch (e) { return null; } })();
    if (!s) return "";
    const c = s.content;
    if (!c || c === "none" || c === "normal") return "";
    const m = c.match(/^attr\(([\w-]+)\)$/);
    if (m) return norm(el.getAttribute(m[1]));
    const q = c.match(/^"(.*)"$/);
    return q ? norm(q[1].replace(/\\"/g, '"')) : "";
  };

  const isDisabled = (el) => el.disabled === true || el.getAttribute("aria-disabled") === "true" ||
    el.hasAttribute("data-unavailable") || !!el.closest("fieldset[disabled], [inert]");

  const isInteractive = (el) => {
    const t = el.tagName;
    if (t === "A" && el.hasAttribute("href")) return true;
    if (["BUTTON", "INPUT", "SELECT", "TEXTAREA", "SUMMARY", "IFRAME", "FRAME"].includes(t)) return !(t === "INPUT" && el.type === "hidden");
    const r = el.getAttribute("role");
    if (r && INTERACTIVE_ROLES.has(r)) return true;
    if (el.hasAttribute("onclick")) return true;
    if (el.isContentEditable && el.getAttribute("contenteditable") !== null) return true;
    const ti = el.getAttribute("tabindex");
    if (ti !== null && Number(ti) >= 0 && !!el.getAttribute("aria-haspopup")) return true;
    // SPA controls: no role, a JS listener and a pointer cursor; cursor inherits, so only the outermost element
    if (t === "BODY" || t === "HTML" || el.closest("a[href],button,[role=button],[role=link]")) return false;
    const s = style(el), ps = el.parentElement && style(el.parentElement);
    return !!s && s.cursor === "pointer" && !(ps && ps.cursor === "pointer") && !!norm(el.textContent);
  };

  const ref = (el) => {
    let r = el.getAttribute("data-agent-ref");
    if (!r) { r = prefix + "e" + (++window.__agentRefSeq); el.setAttribute("data-agent-ref", r); }
    return r;
  };

  const describeControl = (el) => {
    const t = el.tagName;
    const role = el.getAttribute("role");
    const flags = [];
    if (isDisabled(el)) flags.push("deaktiviert");
    if (el.required || el.getAttribute("aria-required") === "true") flags.push("Pflicht");
    if (el.readOnly) flags.push("schreibgeschützt");
    const ex = el.getAttribute("aria-expanded");
    if (ex !== null) flags.push(ex === "true" ? "aufgeklappt" : "zugeklappt");
    if (el.getAttribute("aria-haspopup")) flags.push("Menü");
    if (el.getAttribute("aria-current")) flags.push("aktuell");
    if (el.getAttribute("aria-selected") === "true") flags.push("ausgewählt");
    const fl = flags.length ? " (" + flags.join(", ") + ")" : "";
    const label = labelOf(el);
    const R = "[" + ref(el) + "] ";
    if (t === "A") {
      const href = el.getAttribute("href") || "";
      const tgt = el.getAttribute("target") === "_blank" ? " [neues Fenster]" : "";
      const dl = el.hasAttribute("download") ? " [Download]" : "";
      return R + 'link "' + label + '" → ' + href + tgt + dl + fl;
    }
    if (t === "IFRAME" || t === "FRAME") {
      const src = el.getAttribute("src") || "";
      const empty = el.hasAttribute("srcdoc") && !norm(el.getAttribute("srcdoc")) ? " [srcdoc leer — Inhalt über src öffnen]" : "";
      return R + 'iframe "' + norm(el.getAttribute("title") || el.getAttribute("name")) + '" → ' + src + empty;
    }
    if (t === "SELECT") {
      const opts = [...el.options].map((o) => norm(o.label || o.text) + (o.selected ? "*" : "") + (o.disabled ? "(deaktiviert)" : ""));
      const cur = el.selectedOptions.length ? norm(el.selectedOptions[0].text) : "";
      return R + 'select "' + label + '" = "' + cur + '" Optionen: [' + opts.join(" | ") + "]" + fl;
    }
    if (t === "TEXTAREA") return R + 'textbox "' + label + '" = "' + norm(el.value) + '"' + fl;
    if (t === "INPUT") {
      const ty = (el.type || "text").toLowerCase();
      if (ty === "checkbox" || ty === "radio") return R + ty + ' "' + label + '" ' + (el.checked ? "[x]" : "[ ]") + fl;
      if (ty === "range") return R + 'slider "' + label + '" = ' + el.value + " (" + el.min + "–" + el.max + ")" + fl;
      if (["submit", "button", "reset", "image"].includes(ty)) return R + 'button "' + label + '"' + fl;
      if (ty === "file") return R + 'file "' + label + '"' + fl;
      const v = ty === "password" ? (el.value ? "••••" : "") : norm(el.value);
      return R + (ty === "text" ? "textbox" : ty) + ' "' + label + '" = "' + v + '"' + fl;
    }
    if (t === "SUMMARY") {
      const d = el.parentElement;
      return R + 'summary "' + label + '"' + (d && d.tagName === "DETAILS" && !d.open ? " (eingeklappt)" : "") + fl;
    }
    if (role === "checkbox" || role === "radio" || role === "switch")
      return R + role + ' "' + label + '" ' + (el.getAttribute("aria-checked") === "true" ? "[x]" : "[ ]") + fl;
    if (role === "slider") return R + 'slider "' + label + '" = ' + (el.getAttribute("aria-valuenow") || "") + fl;
    return R + (role || (t === "BUTTON" ? "button" : "clickable")) + ' "' + label + '"' + fl;
  };

  // logical order: aria-posinset, else CSS order inside flex/grid containers
  const orderedChildren = (node) => {
    const kids = [...(node.shadowRoot ? node.shadowRoot.childNodes : []), ...node.childNodes];
    if (node.nodeType !== 1) return kids;
    const ps = style(node);
    const flex = ps && /flex|grid/.test(ps.display);
    const keyed = kids.map((k, i) => {
      let key = 0;
      if (k.nodeType === 1) {
        const pos = k.getAttribute("aria-posinset");
        if (pos !== null && !isNaN(Number(pos))) key = Number(pos);
        else if (flex) { const s = style(k); key = s ? Number(s.order) || 0 : 0; }
      }
      return { k, i, key };
    });
    if (keyed.every((x) => x.key === 0)) return kids;
    return keyed.sort((a, b) => a.key - b.key || a.i - b.i).map((x) => x.k);
  };

  const lines = [];
  let cur = "";
  const flush = () => { const t = cur.replace(/[ \t]+/g, " ").trim(); if (t) lines.push(t); cur = ""; };
  const emit = (s) => { if (s) cur += (cur && !cur.endsWith(" ") ? " " : "") + s; };

  const cellText = (cell) => {
    const save = cur, saveLen = lines.length;
    cur = "";
    walk(cell, true);
    flush();
    const t = lines.splice(saveLen).join(" / ");
    cur = save;
    return t;
  };

  const walk = (node, inline, collapsed) => {
    if (node.nodeType === 3) {
      // preformatted text (CSV/TXT shown in a <pre>): keep its rows as lines
      const ps = node.parentElement && style(node.parentElement);
      if (ps && /^pre/.test(ps.whiteSpace) && node.data.includes("\n")) {
        node.data.split("\n").forEach((l, i) => { if (i) flush(); emit(norm(l)); });
        return;
      }
      emit(norm(node.data)); return;
    }
    if (node.nodeType !== 1 && node.nodeType !== 11) return;
    if (node.nodeType === 11) { orderedChildren(node).forEach((c) => walk(c, inline, collapsed)); return; }
    const el = node, t = el.tagName;
    if (SKIP.has(t)) return;
    if (t === "svg" || t === "SVG") {
      if (IMGROLE.has(el.getAttribute("role")) && el.getAttribute("aria-label")) emit("[Bild: " + norm(el.getAttribute("aria-label")) + "]");
      return;
    }
    if (isTooltip(el)) {
      // attached to its owner; unattached, non-decoy tooltips are printed inline
      if (!(el.id && describedBy.has(el.id)) && el.getAttribute("aria-hidden") !== "true" && !el.hasAttribute("hidden")) {
        const tt = tipText(el); if (tt) emit("(Tooltip: " + tt + ")");
      }
      return;
    }
    // closed <details>: content is not rendered, so computed styles say nothing — keep it, mark it
    const inClosedDetails = collapsed;
    if (isDecoy(el) && !(inClosedDetails && !el.hasAttribute("hidden") && el.getAttribute("aria-hidden") !== "true")) return;

    const role = el.getAttribute("role");
    const block = !inline && (BLOCK.has(t) || (style(el) && /block|flex|grid|list-item|table/.test(style(el).display)));
    if (block) flush();

    if (t === "DIALOG" || role === "dialog" || role === "alertdialog") {
      flush();
      const modal = (t === "DIALOG" && el.matches(":modal")) || el.getAttribute("aria-modal") === "true";
      lines.push("⟦" + (modal ? "MODALER DIALOG" : "DIALOG") + ' "' + labelOf(el).slice(0, 80) + '"⟧');
      orderedChildren(el).forEach((c) => walk(c, false, collapsed));
      flush(); lines.push("⟦/DIALOG⟧");
      return;
    }
    if (t === "CANVAS" || (role && IMGROLE.has(role)) || t === "IMG" || t === "OUTPUT") {
      const l = norm(el.getAttribute("aria-label")) || (t === "IMG" ? norm(el.getAttribute("alt")) : "");
      if (t === "OUTPUT") { emit(l || textOf(el)); return; }
      if (l) emit("[Bild: " + l + "]");
      else if (t === "CANVAS") emit("[Canvas ohne Text: " + ref(el) + " — read_canvas]");
      else if (t !== "IMG") orderedChildren(el).forEach((c) => walk(c, inline, collapsed));
      return;
    }
    if (t === "TABLE") {
      flush();
      const cap = el.querySelector(":scope > caption");
      if (cap) lines.push("Tabelle: " + norm(cap.textContent));
      for (const row of el.rows) {
        if (isDecoy(row) && !collapsed) continue;
        const cells = [...row.cells].filter((c) => !(isDecoy(c) && !collapsed)).map(cellText);
        if (cells.some(Boolean)) lines.push("| " + cells.join(" | ") + " |");
      }
      return;
    }
    if (role === "row") {  // ARIA grids built from divs: one line per row, like a <table>
      const cells = [...el.querySelectorAll("[role=cell],[role=gridcell],[role=columnheader],[role=rowheader]")]
        .filter((c) => c.closest("[role=row]") === el && !(isDecoy(c) && !collapsed));
      if (cells.length) {
        flush();
        const texts = cells.map(cellText);
        if (texts.some(Boolean)) lines.push("| " + texts.join(" | ") + " |");
        return;
      }
    }
    if (/^H[1-6]$/.test(t)) { flush(); cur = "#".repeat(Number(t[1])) + " "; }
    if (t === "LI") emit("•");
    if (t === "FIELDSET" && (el.disabled || el.inert)) { flush(); lines.push("[gesperrt bis Freigabe:]"); }
    if (role === "status" || role === "alert" || el.getAttribute("aria-live")) {
      if (!richText(el)) return;
      emit(role === "alert" ? "(Meldung:)" : "(Status:)");
    }
    // a label wrapping its control: the control line already carries the label text
    if (t === "LABEL" && el.querySelector("input,select,textarea")) {
      orderedChildren(el).forEach((c) => {
        if (c.nodeType === 1 && (isInteractive(c) || c.querySelector("input,select,textarea,a[href],button"))) walk(c, inline, collapsed);
      });
      if (block) flush();
      return;
    }

    const before = pseudo(el, "::before");
    if (before) emit(before);

    const ex = extras(el);
    if (isInteractive(el)) {
      emit(describeControl(el));
      if (ex) emit(ex);
      // controls nested inside (rare: clickable rows/cards) are listed on their own
      if (["A", "BUTTON", "SELECT", "TEXTAREA", "INPUT", "IFRAME", "FRAME", "SUMMARY"].includes(t) ||
          !el.querySelector("input,select,textarea,a[href],button,[role=button],[role=link]")) {
        const after = pseudo(el, "::after"); if (after) emit(after);
        if (block) flush();
        return;
      }
    } else if (ex) emit(ex);
    if (t === "TIME") {
      const dt = el.getAttribute("datetime");
      emit(textOf(el) + (dt ? " (" + dt + ")" : ""));
      return;
    }
    if (t === "BR") { flush(); return; }
    if (t === "DETAILS") {
      const open = el.open;
      for (const c of orderedChildren(el)) {
        if (c.nodeType === 1 && c.tagName === "SUMMARY") { walk(c, false, collapsed); if (!open) { flush(); lines.push("[eingeklappter Inhalt:]"); } }
        else walk(c, false, collapsed || !open);
      }
      flush(); if (!open) lines.push("[/eingeklappt]");
      return;
    }
    orderedChildren(el).forEach((c) => walk(c, inline, collapsed));
    const after = pseudo(el, "::after"); if (after) emit(after);
    if (block) flush();
  };

  let root = document.body || document.documentElement;
  if (args && args.scope) {
    const s = document.querySelector('[data-agent-ref="' + args.scope + '"]');
    if (!s) return { error: "scope " + args.scope + " nicht gefunden" };
    root = s;
  }
  if (!root) return { text: "", title: document.title };
  walk(root, false, false);
  flush();
  // collapse repeated identical lines (boilerplate)
  const out = [];
  for (const l of lines) if (out[out.length - 1] !== l) out.push(l);
  return { text: out.join("\n"), title: norm(document.title), refs: window.__agentRefSeq };
}
