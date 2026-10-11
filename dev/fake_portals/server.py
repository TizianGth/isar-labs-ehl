"""Mini RFQ Portals Test Environment Server

Implements four synthetic RFQ portals (tender, specification, erp, mail)
supporting classic, modern, and legacy profiles across synthetic test cases.
"""

import argparse
import copy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import escape
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import re
from pathlib import Path
import secrets
import sys
import threading
import time
from urllib.parse import parse_qs, unquote, urlparse
import uuid

def load_fixture_cases():
    """Fail explicitly if the sole fixture source is missing or invalid."""
    return json.loads((Path(__file__).resolve().parent / "cases.json").read_text(encoding="utf-8"))

def parse_flexible_decimal(val):
    if val is None:
        return None
    s = str(val).strip().replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") < s.rfind("."):
            s = s.replace(",", "")
        else:
            s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        n = Decimal(s)
        if not n.is_finite():
            raise ValueError("Numeric values must be finite")
        return n
    except InvalidOperation:
        raise ValueError(f"Malformed numeric value: {val}")

def clean_num(val, prefer_int=False):
    if val is None or str(val).strip() == "":
        return None
    d = parse_flexible_decimal(val)
    if prefer_int and d == d.to_integral():
        return int(d)
    return float(d)


def draft_summary(draft):
    """Show the recorded draft again on the final signing screen."""
    if not draft:
        return '<p>No saved proposal.</p>'
    fields = ('position', 'material_number', 'quantity', 'unit_price', 'base_unit_price',
              'surcharge_per_unit', 'packaging_per_unit', 'rebate_total', 'lead_time_weeks')
    rows = ''.join('<tr>' + ''.join(f'<td>{escape(str(row.get(key)))}</td>' for key in fields) + '</tr>'
                   for row in draft['positions'])
    return (f'<h3>Recorded proposal {escape(draft["tender_reference"])}</h3>'
            f'<p>Offer type: {"main" if draft["decision"] == "bid" else "no_bid"}; '
            f'Discount percent: {draft["discount_percent"]}; Total net EUR: {draft["total_net"]} (en-US)</p>'
            f'<p>Tender documents accepted: {draft["accept_documents"]}; Own terms declared: {draft["own_terms"]}</p>'
            '<table><thead><tr>' + ''.join(f'<th>{key}</th>' for key in fields) +
            '</tr></thead><tbody>' + rows + '</tbody></table>')


def restore_form(content, draft, positions):
    """Edit restores recorded values, not initial placeholder prices."""
    if not draft:
        return content
    values = {'bidder_company': draft['bidder_company'], 'bidder_vat': draft['bidder_vat'],
              'discount_percent': draft['discount_percent'], 'total_net': draft['total_net']}
    rows = {p['position']: p for p in draft['positions']}
    for i, pos in enumerate(positions):
        row = rows.get(pos['position'], {})
        for field in ('quantity', 'unit_price', 'base_unit_price', 'surcharge_per_unit',
                      'packaging_per_unit', 'rebate_total', 'lead_time_weeks'):
            values[f'pos_{i}_{field}'] = row.get(field, '0')
    for name, value in values.items():
        pattern = rf'(<input[^>]*name="{re.escape(name)}"[^>]*value=")[^"]*(")'
        content = re.sub(pattern, lambda m: m[1] + escape(str(value)) + m[2], content)
    selections = {'offer_type': draft['decision'], **{
        f'pos_{i}_material': rows.get(pos['position'], {}).get('material_number', '') for i, pos in enumerate(positions)}}
    for name, value in selections.items():
        pattern = rf'(<select name="{re.escape(name)}">)(.*?)(</select>)'
        def select(m):
            options = m[2].replace(' selected', '')
            options = options.replace(f'value="{escape(str(value))}"', f'value="{escape(str(value))}" selected')
            return m[1] + options + m[3]
        content = re.sub(pattern, select, content, flags=re.S)
    flags = {'accept_documents': draft['accept_documents'], 'own_terms': draft['own_terms'], **{
        f'pos_{i}_omit': pos['position'] not in rows for i, pos in enumerate(positions)}}
    for name, checked in flags.items():
        content = re.sub(rf'<input[^>]*name="{re.escape(name)}"[^>]*>',
                         lambda m: m[0].replace(' checked', '').replace('>', ' checked>' if checked else '>'), content)
    return content

def render_layout(title, body_content, profile='classic', role='tender', current_path='', nav_links=None):
    if profile == 'modern':
        translations = {
            'Ausschreibungsübersicht': 'Tender overview', 'Ausschreibungspositionen': 'Tender positions',
            'Ausschreibungsreferenz': 'Tender reference', 'Auftraggeber': 'Organisation',
            'Positionen anzeigen': 'View positions', 'Fragen & Antworten': 'Questions and answers',
            'Angebot vorbereiten': 'Prepare proposal', 'Zurück zur Übersicht': 'Back to overview',
            'Vorherige Seite': 'Previous page', 'Nächste Seite': 'Next page',
            'Anforderungsreferenz': 'Requirement reference', 'Nummer': 'Number', 'Inhalt': 'Content',
            'Antwort #': 'Answer #', 'Seite ': 'Page ', 'Datum': 'Date', 'Einheit': 'Unit',
            'Angebot erstellen für': 'Create proposal for', 'Bieterunternehmen': 'Bidder company',
            'USt-IdNr.': 'VAT ID', 'Angebotstyp': 'Offer type', 'Hauptangebot': 'Main offer',
            'Kein Angebot': 'No bid', 'Auftragsrabatt in Prozent': 'Order discount percent',
            'Nettogesamtpreis': 'Total net', 'Materialnummer': 'Material number', 'Menge': 'Quantity',
            'Nettostückpreis': 'Net unit price', 'Grundpreis pro Einheit': 'Base unit price',
            'Zuschlag pro Einheit': 'Surcharge per unit', 'Verpackung pro Einheit': 'Packaging per unit',
            'Projektrabatt gesamt': 'Project rebate total', 'Lieferzeit (Wochen)': 'Delivery weeks',
            'Verpflichtende Erklärungen': 'Required declarations',
            'Ich akzeptiere die Ausschreibungsunterlagen vollumfänglich.': 'I accept the tender documents.',
            'Eigene AGB einbinden (Ausschlussgrund)': 'Declare own terms (disqualifies proposal)',
            'Angebot prüfen (Entwurf speichern)': 'Review proposal (save draft)',
            'Prüfung des Angebotsentwurfs': 'Proposal review', 'Entscheidung': 'Decision',
            'Bieter:': 'Bidder:', 'Auftragsrabatt': 'Order discount', 'Gesamt netto': 'Total net',
            'Erklärungen': 'Declarations', 'Ausschreibungsbedingungen akzeptiert': 'Tender documents accepted',
            'Eigene AGB': 'Own terms', 'Stückpreis': 'Unit price', 'Grundpreis': 'Base price',
            'Zuschlag': 'Surcharge', 'Verpackung': 'Packaging', 'Rabatt': 'Rebate', 'Lieferzeit': 'Delivery',
            'Bearbeiten / Zurück': 'Edit / back', 'Signaturcode (TAN) anfordern': 'Request signing code (TAN)',
            'Verbindlich einreichen': 'Submit binding proposal',
            'Bestätigung der Angebotsabgabe': 'Proposal submission confirmed',
            'Ihr Angebot wurde erfolgreich erfasst.': 'Your proposal has been received.',
            'Eingangsbestätigung': 'Receipt', 'Zurück zum Portal': 'Back to portal',
            'ERP Artikel-Katalogsuche': 'ERP material catalogue search',
            'Kataloggruppe eingeben': 'Enter catalogue group', 'Kataloggruppe suchen': 'Search catalogue group',
            'Suchergebnisse für Gruppe': 'Search results for group', 'Kataloggruppe': 'Catalogue group',
            'Befestigungsart': 'Mounting', 'Befestigung': 'Mounting', 'Freier Bestand': 'Free stock',
            'Aktion': 'Action', 'Status-Legende': 'Status legend', 'Materialdetail': 'Material detail',
            'Zurück zur Suche': 'Back to search', 'Technische Spezifikation': 'Technical specification',
            'Abmessungen': 'Dimensions', 'Zertifizierung': 'Certification',
            'Bestände & Logistik': 'Stock and logistics', 'Konditionen & Preisstaffeln': 'Conditions and quantity tiers',
            'Gültigkeit': 'Validity', 'Währung': 'Currency', 'Preiseinheit': 'Price unit',
            'Staffeln': 'Quantity tiers', 'Wochen': 'weeks', 'Referenz': 'Reference',
            'Revisionshistorie': 'Revision history', 'Anforderungen': 'Requirements',
            'Anforderung': 'Requirement', 'Dokumentenregister': 'Document register',
            'Historische Version ansehen': 'View superseded revision',
        }
        for old, new in translations.items():
            body_content = body_content.replace(old, new)
        if role == 'erp' and body_content.count('<h3>') == 3:
            prefix, *sections = body_content.split('<h3>')
            buttons, panels = [], []
            for i, section in enumerate(sections):
                label, detail = section.split('</h3>', 1)
                selected = 'true' if i == 0 else 'false'
                buttons.append(f'<button type="button" role="tab" aria-selected="{selected}" class="tab-btn" '
                               f'onclick="switchTab(this, {i})">{label}</button>')
                panels.append(f'<section role="tabpanel" id="panel-{i}" style="display:{"block" if i == 0 else "none"}">'
                              f'<h3>{label}</h3>{detail}</section>')
            body_content = prefix + '<div role="tablist">' + ''.join(buttons) + '</div>' + ''.join(panels) + '''
<script>function switchTab(button,n){document.querySelectorAll('[role="tab"]').forEach(b=>b.setAttribute('aria-selected','false'));
button.setAttribute('aria-selected','true'); document.querySelectorAll('[role="tabpanel"]').forEach((p,i)=>p.style.display=i===n?'block':'none');}</script>'''
    nav_html = ""
    if nav_links:
        items = []
        for label, href in nav_links:
            cls = "active" if href == current_path else ""
            items.append(f'<a href="{escape(href)}" class="nav-link {cls}">{escape(label)}</a>')
        nav_html = " &nbsp;|&nbsp; ".join(items)

    if profile == 'classic':
        return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>{escape(title)} - Portal</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f7f9fa; color: #111; }}
    header {{ background: #003366; color: #fff; padding: 12px 18px; border-radius: 4px; margin-bottom: 15px; }}
    header h1 {{ margin: 0; font-size: 20px; }}
    nav {{ background: #eaedf1; padding: 10px 14px; border: 1px solid #ccd0d5; border-radius: 4px; margin-bottom: 20px; }}
    a {{ color: #004085; text-decoration: none; font-weight: bold; }}
    a:hover {{ text-decoration: underline; }}
    .container {{ background: #fff; border: 1px solid #dcdcdc; border-radius: 4px; padding: 20px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
    th, td {{ border: 1px solid #bbb; padding: 8px 12px; text-align: left; }}
    th {{ background: #e9ecef; }}
    .btn {{ background: #003366; color: white; border: none; padding: 8px 16px; border-radius: 3px; cursor: pointer; text-decoration: none; display: inline-block; }}
    .btn:hover {{ background: #002244; }}
    .alert {{ padding: 10px; margin: 10px 0; border: 1px solid #ffa; background: #ffffd0; }}
    .legend {{ font-size: 0.85em; color: #555; margin-top: 10px; }}
  </style>
</head>
<body>
  <header><h1>{escape(title)}</h1></header>
  <nav>{nav_html}</nav>
  <div class="container">
    {body_content}
  </div>
</body>
</html>"""
    elif profile == 'modern':
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{escape(title)} - Enterprise RFQ</title>
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; margin: 0; background-color: #f4f6f9; color: #212529; }}
    .app-header {{ background: #1a202c; color: #edf2f7; padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; }}
    .app-header h1 {{ margin: 0; font-size: 1.25rem; font-weight: 600; }}
    .app-nav {{ background: #ffffff; border-bottom: 1px solid #e2e8f0; padding: 10px 24px; font-size: 0.95rem; }}
    .app-nav a {{ color: #4a5568; text-decoration: none; margin-right: 18px; font-weight: 500; }}
    .app-nav a:hover, .app-nav a.active {{ color: #2b6cb0; text-decoration: underline; }}
    .main-wrap {{ max-width: 1040px; margin: 24px auto; padding: 0 16px; }}
    .card {{ background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); padding: 24px; margin-bottom: 20px; }}
    table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
    th, td {{ padding: 10px 14px; border-bottom: 1px solid #edf2f7; text-align: left; }}
    th {{ background: #f7fafc; color: #4a5568; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.05em; }}
    .btn {{ background: #3182ce; color: #ffffff; border: none; padding: 9px 18px; border-radius: 6px; font-weight: 500; cursor: pointer; text-decoration: none; display: inline-block; }}
    .btn:hover {{ background: #2b6cb0; }}
    .tab-btn {{ background: #edf2f7; border: 1px solid #cbd5e0; padding: 8px 14px; border-radius: 4px; cursor: pointer; margin-right: 6px; font-size: 0.9rem; }}
    .tab-btn.active {{ background: #3182ce; color: #fff; border-color: #3182ce; }}
    .tab-panel {{ display: none; margin-top: 16px; }}
    .tab-panel.active {{ display: block; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 600; background: #e2e8f0; color: #4a5568; }}
    .legend {{ font-size: 0.85rem; color: #718096; margin-top: 12px; }}
  </style>
</head>
<body>
  <header class="app-header">
    <h1>{escape(title)}</h1>
    <div class="badge">Profile: Modern</div>
  </header>
  <nav class="app-nav">{nav_html}</nav>
  <main class="main-wrap">
    <div class="card">
      {body_content}
    </div>
  </main>
</body>
</html>"""
    else:
        return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{escape(title)} - Legacy Portal</title>
  <style>
    body {{ font-family: monospace, serif; margin: 10px; background-color: #e0e0e0; color: #000; }}
    table.layout {{ width: 100%; border: 2px solid #666; background-color: #fff; }}
    th.header {{ background-color: #333366; color: #ffffff; padding: 6px; text-align: left; font-size: 14pt; }}
    td.nav {{ background-color: #cccccc; border-bottom: 2px solid #666; padding: 6px; }}
    td.content {{ padding: 15px; background-color: #ffffff; }}
    table.data {{ border: 1px solid #999; border-collapse: collapse; width: 100%; }}
    table.data th {{ border: 1px solid #999; background: #dddddd; padding: 4px; text-align: left; }}
    table.data td {{ border: 1px solid #999; padding: 4px; text-align: left; }}
    .btn {{ background: #dddddd; border: 2px outset #ffffff; color: #000; padding: 4px 10px; cursor: pointer; text-decoration: none; font-weight: bold; }}
    .btn:active {{ border: 2px inset #ffffff; }}
    .legend {{ font-size: 9pt; color: #333; margin-top: 8px; }}
  </style>
</head>
<body>
  <table class="layout" cellpadding="0" cellspacing="0">
    <tr><th class="header">{escape(title)} [LEGACY SYSTEM]</th></tr>
    <tr><td class="nav">{nav_html}</td></tr>
    <tr><td class="content">{body_content}</td></tr>
  </table>
</body>
</html>"""

def make_handler_class(cluster, role):
    class RoleHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        @property
        def profile(self):
            return cluster.profile

        @property
        def case(self):
            return cluster.case

        def _cookie_name(self):
            return f"rfq_session_{role}"

        def _is_authenticated(self):
            hdr = self.headers.get("Cookie")
            if not hdr:
                return False
            c = cookies.SimpleCookie()
            try:
                c.load(hdr)
            except Exception:
                return False
            name = self._cookie_name()
            if name not in c:
                return False
            token = c[name].value
            with cluster.lock:
                return token in cluster.sessions[role]

        def _send_html(self, status, content):
            b = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return True

        def _redirect(self, location, status=303, set_cookies=None):
            self.send_response(status)
            self.send_header("Location", location)
            if set_cookies:
                for sc in set_cookies:
                    self.send_header("Set-Cookie", sc)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True

        def _login_url(self):
            if self.profile == 'classic':
                return "/anmeldung"
            elif self.profile == 'legacy':
                return "/auth.cgi"
            return "/login"

        def _render_login(self, error=None):
            title = "Anmeldung" if self.profile == 'classic' else ("Login" if self.profile == 'modern' else "System Login")
            user_lbl = "Benutzername" if self.profile == 'classic' else "Username"
            pass_lbl = "Passwort" if self.profile == 'classic' else "Password"
            submit_lbl = "Anmelden" if self.profile == 'classic' else "Sign In"
            err_html = f'<div style="color: red; margin-bottom: 12px; font-weight: bold;">{escape(error)}</div>' if error else ""
            form_action = self._login_url()
            content = f"""
            <h2>{title} - Role: {role.title()}</h2>
            {err_html}
            <form method="POST" action="{form_action}">
              <p><label>{user_lbl}: <br><input type="text" name="username" value="demo" required></label></p>
              <p><label>{pass_lbl}: <br><input type="password" name="password" value="synthetic-only" required></label></p>
              <p><button type="submit" class="btn">{submit_lbl}</button></p>
            </form>
            """
            return render_layout(title, content, profile=self.profile, role=role, current_path=self._login_url())

        def _get_nav_links(self):
            p = self.profile
            if role == 'tender':
                if p == 'classic':
                    return [
                        ("Übersicht", "/uebersicht"),
                        ("Positionen", "/positionen"),
                        ("Fragen & Antworten", "/fragen-antworten"),
                        ("Angebot einreichen", "/angebot-einreichen"),
                        ("Abmelden", "/abmelden")
                    ]
                elif p == 'modern':
                    return [
                        ("Dashboard", "/dashboard"),
                        ("Positions", "/positions"),
                        ("Q&A", "/qa"),
                        ("Submit Proposal", "/proposal"),
                        ("Sign Out", "/logout")
                    ]
                else:
                    return [
                        ("RFQ Overview", "/portal/rfq_main.html"),
                        ("Positions Table", "/portal/rfq_items.html"),
                        ("FAQ & QA", "/portal/rfq_faq.html"),
                        ("Proposal Entry", "/portal/rfq_bid.html"),
                        ("Logout", "/logout")
                    ]
            elif role == 'specification':
                if p == 'classic':
                    return [
                        ("Dokumentenregister", "/dokumente"),
                        ("Revisionshistorie", "/revisionen"),
                        ("Anforderung REQ-A", "/anforderungen/teil1"),
                        ("Anforderung REQ-B", "/anforderungen/teil2"),
                        ("Abmelden", "/abmelden")
                    ]
                elif p == 'modern':
                    return [
                        ("Specifications", "/specifications"),
                        ("Revision History", "/revisions"),
                        ("Requirement A", "/requirements/req-a"),
                        ("Requirement B", "/requirements/req-b"),
                        ("Sign Out", "/logout")
                    ]
                else:
                    return [
                        ("Portal Frame", "/frameset.html"),
                        ("Tech Index", "/legacy_spec/docs.html"),
                        ("Revisions", "/legacy_spec/revs.html"),
                        ("REQ-A", "/legacy_spec/req1.html"),
                        ("REQ-B", "/legacy_spec/req2.html"),
                        ("Logout", "/logout")
                    ]
            elif role == 'erp':
                if p == 'classic':
                    return [("Katalogsuche", "/katalog"), ("Abmelden", "/abmelden")]
                elif p == 'modern':
                    return [("Catalog Search", "/catalog"), ("Sign Out", "/logout")]
                else:
                    return [("ERP Frameset", "/frameset.html"), ("Catalog Find", "/erp_app/find.html"), ("Logout", "/logout")]
            elif role == 'mail':
                if p == 'classic':
                    return [("Posteingang", "/postfach/posteingang"), ("Archiv", "/postfach/archiv"), ("Abmelden", "/abmelden")]
                elif p == 'modern':
                    return [("Inbox", "/inbox"), ("Archive", "/archive"), ("Sign Out", "/logout")]
                else:
                    return [("Webmail Inbox", "/webmail/inbox.html"), ("Archive Folder", "/webmail/archive.html"), ("Logout", "/logout")]
            return []

        def _home_path(self):
            links = self._get_nav_links()
            return links[0][1] if links else "/"

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            with cluster.lock:
                cluster.events.append({'action': 'request', 'role': role, 'method': 'GET', 'path': self.path})

            login_paths = {"/login", "/anmeldung", "/auth.cgi"}
            if path in login_paths:
                return self._send_html(200, self._render_login())

            if path in {"/logout", "/abmelden"}:
                hdr = self.headers.get("Cookie")
                if hdr:
                    c = cookies.SimpleCookie()
                    try:
                        c.load(hdr)
                        name = self._cookie_name()
                        if name in c:
                            token = c[name].value
                            with cluster.lock:
                                cluster.sessions[role].discard(token)
                    except Exception:
                        pass
                cookie_hdr = f"{self._cookie_name()}=; Path=/; Max-Age=0; HttpOnly"
                return self._redirect(self._login_url(), set_cookies=[cookie_hdr])

            if not self._is_authenticated():
                return self._redirect(self._login_url())

            if path == "/":
                return self._redirect(self._home_path())

            handler_method = getattr(self, f"_handle_{role}_get", None)
            if handler_method:
                handled = handler_method(path, query)
                if handled:
                    return

            return self._send_html(404, render_layout("404 Not Found", "<h2>404 Not Found</h2><p>Page does not exist.</p>", profile=self.profile, role=role))

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path
            with cluster.lock:
                cluster.events.append({'action': 'request', 'role': role, 'method': 'POST', 'path': path})

            length = int(self.headers.get("Content-Length", 0))
            body_bytes = self.rfile.read(length)
            form = parse_qs(body_bytes.decode("utf-8", errors="replace"), keep_blank_values=True)

            login_paths = {"/login", "/anmeldung", "/auth.cgi"}
            if path in login_paths:
                username = form.get("username", [""])[0]
                password = form.get("password", [""])[0]
                if username == "demo" and password == "synthetic-only":
                    token = secrets.token_hex(16)
                    with cluster.lock:
                        cluster.sessions[role].add(token)
                        cluster.events.append({"time": datetime.now(timezone.utc).isoformat(), "action": "login", "role": role, "status": "success"})
                    cookie_hdr = f"{self._cookie_name()}={token}; Path=/; HttpOnly"
                    return self._redirect(self._home_path(), set_cookies=[cookie_hdr])
                else:
                    with cluster.lock:
                        cluster.events.append({"time": datetime.now(timezone.utc).isoformat(), "action": "login", "role": role, "status": "failed"})
                    return self._send_html(200, self._render_login(error="Invalid credentials / Ungültige Anmeldedaten"))

            if not self._is_authenticated():
                return self._redirect(self._login_url())

            handler_method = getattr(self, f"_handle_{role}_post", None)
            if handler_method:
                handled = handler_method(path, form)
                if handled:
                    return

            return self._send_html(404, render_layout("404 Not Found", "<h2>404 Not Found</h2>", profile=self.profile, role=role))

        # --- Tender Origin Handlers ---
        def _handle_tender_get(self, path, query):
            p = self.profile
            overview_paths = {"classic": "/uebersicht", "modern": "/dashboard", "legacy": "/portal/rfq_main.html"}
            pos_paths = {"classic": "/positionen", "modern": "/positions", "legacy": "/portal/rfq_items.html"}
            qa_paths = {"classic": "/fragen-antworten", "modern": "/qa", "legacy": "/portal/rfq_faq.html"}
            prop_paths = {"classic": "/angebot-einreichen", "modern": "/proposal", "legacy": "/portal/rfq_bid.html"}
            review_paths = {"classic": "/pruefung", "modern": "/review", "legacy": "/portal/rfq_verify.html"}
            sign_paths = {"classic": "/unterschrift", "modern": "/sign", "legacy": "/portal/rfq_sign.html"}
            conf_paths = {"classic": "/bestaetigung", "modern": "/confirmation", "legacy": "/portal/rfq_done.html"}

            if path == overview_paths[p]:
                dt = self.case['scenario_datetime_utc']
                content = f"""
                <h2>Ausschreibungsübersicht: {escape(self.case['reference'])}</h2>
                <p><strong>Auftraggeber:</strong> {escape(self.case['company'])}</p>
                <p><strong>Umsatzsteuer-ID:</strong> {escape(self.case['vat_id'])}</p>
                <p><strong>Datum:</strong> <time datetime="{dt}">{dt}</time></p>
                <div style="margin-top: 20px;">
                  <a href="{pos_paths[p]}" class="btn">Positionen anzeigen</a> &nbsp;
                  <a href="{qa_paths[p]}" class="btn">Fragen & Antworten</a> &nbsp;
                  <a href="{prop_paths[p]}" class="btn">Angebot vorbereiten</a>
                </div>
                """
                self._send_html(200, render_layout("Tender Overview", content, p, role, path, self._get_nav_links()))
                return True

            elif path == pos_paths[p]:
                rows = []
                for pos in self.case['positions']:
                    rows.append(f"<tr><td>{escape(pos['position'])}</td><td>{escape(str(pos['quantity']))}</td><td>{escape(pos['unit'])}</td><td>{escape(pos['requirement_ref'])}</td></tr>")
                content = f"""
                <h2>Ausschreibungspositionen ({escape(self.case['reference'])})</h2>
                <table>
                  <thead><tr><th>Position</th><th>Menge</th><th>Einheit</th><th>Anforderungsreferenz</th></tr></thead>
                  <tbody>{''.join(rows)}</tbody>
                </table>
                <p><a href="{overview_paths[p]}" class="btn">Zurück zur Übersicht</a></p>
                """
                self._send_html(200, render_layout("Positions", content, p, role, path, self._get_nav_links()))
                return True

            elif path == qa_paths[p]:
                qa_list = self.case.get('qa', [])
                page_str = query.get('page', query.get('seite', ['1']))[0]
                try:
                    page = max(1, int(page_str))
                except ValueError:
                    page = 1
                per_page = 2
                total_pages = max(1, math.ceil(len(qa_list) / per_page))
                if page > total_pages:
                    page = total_pages
                start_idx = (page - 1) * per_page
                page_items = qa_list[start_idx:start_idx + per_page]

                rows = []
                for item in page_items:
                    num = item.get('number', '')
                    dt = item.get('datetime_utc', '')
                    txt = item.get('text', '')
                    rows.append(f"<tr><td>Antwort #{escape(str(num))}</td><td><time datetime=\"{dt}\">{dt}</time></td><td>{escape(txt)}</td></tr>")

                nav_links = []
                param_name = 'seite' if p == 'classic' else 'page'
                if page > 1:
                    nav_links.append(f'<a href="{qa_paths[p]}?{param_name}={page - 1}" class="btn">Vorherige Seite</a>')
                if page < total_pages:
                    nav_links.append(f'<a href="{qa_paths[p]}?{param_name}={page + 1}" class="btn">Nächste Seite</a>')

                content = f"""
                <h2>Fragen & Antworten (Seite {page} von {total_pages})</h2>
                <table>
                  <thead><tr><th>Nummer</th><th>Datum</th><th>Inhalt</th></tr></thead>
                  <tbody>{''.join(rows)}</tbody>
                </table>
                <div style="margin: 15px 0;">{' &nbsp; '.join(nav_links)}</div>
                <p><a href="{overview_paths[p]}" class="btn">Zurück zur Übersicht</a></p>
                """
                self._send_html(200, render_layout("Q&A", content, p, role, path, self._get_nav_links()))
                return True

            elif path == prop_paths[p]:
                with cluster.lock:
                    draft = copy.deepcopy(cluster.draft) if cluster.draft else None

                mat_options = ['<option value="">-- Select material --</option>']
                for mat in self.case.get('materials', []):
                    m_num = mat['material_number']
                    mat_options.append(f'<option value="{escape(m_num)}">{escape(m_num)} ({escape(mat.get("catalog_group", ""))})</option>')
                mat_opt_html = "\n".join(mat_options)

                fieldsets = []
                for idx, pos in enumerate(self.case['positions']):
                    p_id = pos['position']
                    fieldsets.append(f"""
                    <fieldset style="margin-bottom: 16px; border: 1px solid #ccc; padding: 12px;">
                      <legend><strong>Position {escape(p_id)}</strong> (Bedarf: {escape(str(pos['quantity']))} {escape(pos['unit'])})</legend>
                      <input type="hidden" name="pos_{idx}_position" value="{escape(p_id)}">
                      <p><label><input type="checkbox" name="pos_{idx}_omit" value="1"> Position weglassen / Omit this position</label></p>
                      <p><label>Materialnummer:<br><select name="pos_{idx}_material">{mat_opt_html}</select></label></p>
                      <p><label>Menge:<br><input type="text" name="pos_{idx}_quantity" value="{escape(str(pos['quantity']))}" required></label></p>
                      <p><label>Nettostückpreis (EUR):<br><input type="text" name="pos_{idx}_unit_price" value="0.00" required></label></p>
                      <p><label>Grundpreis pro Einheit (EUR):<br><input type="text" name="pos_{idx}_base_unit_price" value="0.00" required></label></p>
                      <p><label>Zuschlag pro Einheit (EUR):<br><input type="text" name="pos_{idx}_surcharge_per_unit" value="0.00" required></label></p>
                      <p><label>Verpackung pro Einheit (EUR):<br><input type="text" name="pos_{idx}_packaging_per_unit" value="0.00" required></label></p>
                      <p><label>Projektrabatt gesamt (EUR):<br><input type="text" name="pos_{idx}_rebate_total" value="0.00" required></label></p>
                      <p><label>Lieferzeit (Wochen):<br><input type="text" name="pos_{idx}_lead_time_weeks" value="0" required></label></p>
                    </fieldset>
                    """)

                content = f"""
                <h2>Angebot erstellen für {escape(self.case['reference'])}</h2>
                <form method="POST" action="{prop_paths[p]}">
                  <p><label>Bieterunternehmen:<br><input type="text" name="bidder_company" value="{escape(self.case['company'])}" required></label></p>
                  <p><label>USt-IdNr.:<br><input type="text" name="bidder_vat" value="{escape(self.case['vat_id'])}" required></label></p>
                  <p><label>Angebotstyp:<br>
                    <select name="offer_type">
                      <option value="bid">Hauptangebot (bid)</option>
                      <option value="no_bid">Kein Angebot (no_bid)</option>
                    </select>
                  </label></p>
                  <p><label>Auftragsrabatt in Prozent:<br><input type="text" name="discount_percent" value="0.00" required></label></p>
                  <p><label>Nettogesamtpreis (EUR):<br><input type="text" name="total_net" value="0.00" required></label></p>

                  {''.join(fieldsets)}

                  <fieldset style="margin-top: 16px; border: 1px solid #ccc; padding: 12px;">
                    <legend>Verpflichtende Erklärungen</legend>
                    <p><label><input type="checkbox" name="accept_documents" value="1" checked required> Ich akzeptiere die Ausschreibungsunterlagen vollumfänglich.</label></p>
                    <p><label><input type="checkbox" name="own_terms" value="1"> Eigene AGB einbinden (Ausschlussgrund)</label></p>
                  </fieldset>

                  <p style="margin-top: 20px;"><button type="submit" class="btn">Angebot prüfen (Entwurf speichern)</button></p>
                </form>
                """
                content = restore_form(content, draft, self.case['positions'])
                self._send_html(200, render_layout("Proposal Form", content, p, role, path, self._get_nav_links()))
                return True

            elif path == review_paths[p]:
                with cluster.lock:
                    draft = copy.deepcopy(cluster.draft) if cluster.draft else None
                if not draft:
                    content = f"<h2>Kein Entwurf vorhanden</h2><p><a href=\"{prop_paths[p]}\" class=\"btn\">Zum Formular</a></p>"
                    self._send_html(200, render_layout("Review", content, p, role, path, self._get_nav_links()))
                    return True

                pos_rows = []
                for pos in draft.get("positions", []):
                    pos_rows.append(f"<tr><td>{escape(str(pos.get('position')))}</td><td>{escape(str(pos.get('material_number')))}</td><td>{escape(str(pos.get('quantity')))}</td><td>{escape(str(pos.get('unit_price')))}</td><td>{escape(str(pos.get('base_unit_price')))}</td><td>{escape(str(pos.get('surcharge_per_unit')))}</td><td>{escape(str(pos.get('packaging_per_unit')))}</td><td>{escape(str(pos.get('rebate_total')))}</td><td>{escape(str(pos.get('lead_time_weeks')))}</td></tr>")

                tan_req_paths = {"classic": "/tan-anfordern", "modern": "/request-tan", "legacy": "/portal/rfq_tan.html"}
                content = f"""
                <h2>Prüfung des Angebotsentwurfs: {escape(self.case['reference'])}</h2>
                <p><strong>Entscheidung:</strong> {escape(str(draft.get('decision')))}</p>
                <p><strong>Bieter:</strong> {escape(str(draft.get('bidder_company')))} (USt: {escape(str(draft.get('bidder_vat')))})</p>
                <p><strong>Auftragsrabatt:</strong> {escape(str(draft.get('discount_percent')))} %</p>
                <p><strong>Gesamt netto:</strong> {escape(str(draft.get('total_net')))} EUR</p>
                <p><strong>Erklärungen:</strong> Ausschreibungsbedingungen akzeptiert: {draft.get('accept_documents')}, Eigene AGB: {draft.get('own_terms')}</p>
                <table>
                  <thead><tr><th>Position</th><th>Material</th><th>Menge</th><th>Stückpreis</th><th>Grundpreis</th><th>Zuschlag</th><th>Verpackung</th><th>Rabatt</th><th>Lieferzeit</th></tr></thead>
                  <tbody>{''.join(pos_rows)}</tbody>
                </table>
                <div style="margin-top: 20px;">
                  <a href="{prop_paths[p]}" class="btn">Bearbeiten / Zurück</a> &nbsp;
                  <form method="POST" action="{tan_req_paths[p]}" style="display: inline;">
                    <button type="submit" class="btn">Signaturcode (TAN) anfordern</button>
                  </form>
                </div>
                """
                self._send_html(200, render_layout("Review Draft", content, p, role, path, self._get_nav_links()))
                return True

            elif path == sign_paths[p]:
                final_paths = {"classic": "/absenden", "modern": "/finalize", "legacy": "/portal/rfq_submit.html"}
                with cluster.lock:
                    summary = draft_summary(copy.deepcopy(cluster.draft))
                content = f"""
                <h2>Angebot verbindlich signieren und absenden</h2>
                {summary}
                <p>Ein Einmalkennwort (TAN) wurde an Ihr Postfach gesendet. Bitte geben Sie den TAN-Code ein.</p>
                <form method="POST" action="{final_paths[p]}" onsubmit="return confirm('Möchten Sie das Angebot jetzt verbindlich einreichen?');">
                  <p><label>TAN-Code:<br><input type="text" name="tan" required autocomplete="off"></label></p>
                  <p><button type="submit" class="btn">Verbindlich einreichen</button></p>
                </form>
                """
                self._send_html(200, render_layout("Signing Form", content, p, role, path, self._get_nav_links()))
                return True

            elif path == conf_paths[p]:
                with cluster.lock:
                    rcpt = cluster.receipt
                if not rcpt:
                    self._send_html(200, render_layout("Not submitted", '<p>No proposal received. No receipt exists.</p>', p, role))
                    return True
                content = f"""
                <h2>Bestätigung der Angebotsabgabe</h2>
                <div class="alert">
                  <p><strong>Status:</strong> Ihr Angebot wurde erfolgreich erfasst.</p>
                  <p><strong>Eingangsbestätigung (Receipt):</strong> <code id="receipt-id">{escape(str(rcpt))}</code></p>
                  <p><strong>Ausschreibungsreferenz:</strong> {escape(self.case['reference'])}</p>
                </div>
                <p><a href="{overview_paths[p]}" class="btn">Zurück zum Portal</a></p>
                """
                self._send_html(200, render_layout("Confirmation", content, p, role, path, self._get_nav_links()))
                return True

            return False

        def _handle_tender_post(self, path, form):
            p = self.profile
            prop_paths = {"classic": "/angebot-einreichen", "modern": "/proposal", "legacy": "/portal/rfq_bid.html"}
            tan_req_paths = {"classic": "/tan-anfordern", "modern": "/request-tan", "legacy": "/portal/rfq_tan.html"}
            final_paths = {"classic": "/absenden", "modern": "/finalize", "legacy": "/portal/rfq_submit.html"}
            review_paths = {"classic": "/pruefung", "modern": "/review", "legacy": "/portal/rfq_verify.html"}
            sign_paths = {"classic": "/unterschrift", "modern": "/sign", "legacy": "/portal/rfq_sign.html"}
            conf_paths = {"classic": "/bestaetigung", "modern": "/confirmation", "legacy": "/portal/rfq_done.html"}

            if path == prop_paths[p]:
                try:
                    offer_type = form.get("offer_type", ["bid"])[0]
                    if offer_type not in ('bid', 'main', 'no_bid'):
                        raise ValueError('Unknown offer type')
                    decision = "bid" if offer_type in ("bid", "main") else "no_bid"
                    discount = clean_num(form.get("discount_percent", ["0"])[0])
                    total_net = clean_num(form.get("total_net", ["0"])[0])
                    accept_docs = form.get("accept_documents", ["0"])[0] in ("1", "on", "true")
                    own_terms = form.get("own_terms", ["0"])[0] in ("1", "on", "true")

                    positions = []
                    for idx, pos in enumerate(self.case['positions']):
                        p_id = form.get(f"pos_{idx}_position", [pos['position']])[0]
                        omit = form.get(f"pos_{idx}_omit", ["0"])[0] in ("1", "on", "true")
                        if omit:
                            continue

                        mat_num = form.get(f"pos_{idx}_material", [""])[0]
                        qty_raw = form.get(f"pos_{idx}_quantity", [None])[0]
                        unit_price_raw = form.get(f"pos_{idx}_unit_price", [None])[0]
                        base_price_raw = form.get(f"pos_{idx}_base_unit_price", [None])[0]
                        surcharge_raw = form.get(f"pos_{idx}_surcharge_per_unit", [None])[0]
                        packaging_raw = form.get(f"pos_{idx}_packaging_per_unit", [None])[0]
                        rebate_raw = form.get(f"pos_{idx}_rebate_total", [None])[0]
                        lead_time_raw = form.get(f"pos_{idx}_lead_time_weeks", [None])[0]

                        if decision == "no_bid" and qty_raw is None:
                            continue

                        positions.append({
                            "position": p_id,
                            "material_number": mat_num,
                            "quantity": clean_num(qty_raw, prefer_int=True),
                            "unit_price": clean_num(unit_price_raw),
                            "base_unit_price": clean_num(base_price_raw),
                            "surcharge_per_unit": clean_num(surcharge_raw),
                            "packaging_per_unit": clean_num(packaging_raw),
                            "rebate_total": clean_num(rebate_raw),
                            "lead_time_weeks": clean_num(lead_time_raw, prefer_int=True)
                        })
                except Exception as e:
                    self._send_html(400, render_layout("Ungültige Eingabe", f"<h2>Fehler: Ungültiges Zahlenformat</h2><p>{escape(str(e))}</p>", p, role))
                    return True

                with cluster.lock:
                    cluster.draft = {
                        "tender_reference": self.case['reference'],
                        "bidder_company": form.get("bidder_company", [""])[0],
                        "bidder_vat": form.get("bidder_vat", [""])[0],
                        "decision": decision,
                        "currency": "EUR",
                        "discount_percent": discount,
                        "positions": positions,
                        "total_net": total_net,
                        "accept_documents": accept_docs,
                        "own_terms": own_terms
                    }
                    cluster.events.append({"time": datetime.now(timezone.utc).isoformat(), "action": "save_draft"})

                return self._redirect(review_paths[p])

            elif path == tan_req_paths[p]:
                with cluster.lock:
                    if not cluster.draft:
                        self._send_html(400, "<h2>Kein Entwurf vorhanden</h2>")
                        return True

                    cluster.tan_requests += 1
                    req_num = cluster.tan_requests
                    tan_code = f"TAN-{secrets.randbelow(900000) + 100000}"
                    cluster.active_tans[tan_code] = {"req": req_num, "created": datetime.now(timezone.utc).isoformat()}

                    dt = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    tan_mail = {
                        "id": f"tan-msg-{req_num}",
                        "folder": "inbox",
                        "subject": f"Signatur-TAN für {self.case['reference']} (Anforderung #{req_num})",
                        "sender": f"security@{self.case['mailbox'].split('@')[-1]}",
                        "datetime_utc": dt,
                        "body": f"Ausschreibung: {self.case['reference']}\nAnforderungsnummer: #{req_num}\nDatum: {dt}\n\nIhr persönlicher Signatur-Code (TAN) lautet:\n{tan_code}\n\nBitte verwenden Sie diesen Code zur finalen Abgabe."
                    }
                    cluster.messages.insert(0, tan_mail)
                    cluster.events.append({"time": dt, "action": "tan_requested", "req_num": req_num})

                return self._redirect(sign_paths[p])

            elif path == final_paths[p]:
                tan_input = form.get("tan", [""])[0].strip()
                with cluster.lock:
                    if cluster.submitted is not None:
                        return self._redirect(conf_paths[p])

                    draft = copy.deepcopy(cluster.draft) if cluster.draft else None
                    if not draft:
                        self._send_html(400, "<h2>Kein Entwurf vorhanden</h2>")
                        return True

                    if not tan_input or tan_input not in cluster.active_tans:
                        cluster.events.append({"time": datetime.now(timezone.utc).isoformat(), "action": "tan_rejected", "reason": "invalid_tan"})
                        self._send_html(400, render_layout("Fehler", "<h2>Ungültiger oder fehlender TAN-Code</h2>", p, role))
                        return True

                    if not draft.get("accept_documents") or draft.get("own_terms"):
                        cluster.events.append({"time": datetime.now(timezone.utc).isoformat(), "action": "rejected_declarations"})
                        self._send_html(400, render_layout("Fehler", "<h2>Erforderliche Erklärungen nicht erfüllt</h2>", p, role))
                        return True

                    normalized_positions = []
                    for pos in draft.get("positions", []):
                        normalized_positions.append({
                            "position": pos.get("position"),
                            "material_number": pos.get("material_number"),
                            "quantity": pos.get("quantity"),
                            "unit_price": pos.get("unit_price"),
                            "base_unit_price": pos.get("base_unit_price"),
                            "surcharge_per_unit": pos.get("surcharge_per_unit"),
                            "packaging_per_unit": pos.get("packaging_per_unit"),
                            "rebate_total": pos.get("rebate_total"),
                            "lead_time_weeks": pos.get("lead_time_weeks")
                        })

                    cluster.submitted = {
                        "tender_reference": self.case['reference'],
                        "decision": draft.get("decision", "bid"),
                        "currency": "EUR",
                        "discount_percent": draft.get("discount_percent"),
                        "positions": normalized_positions,
                        "total_net": draft.get("total_net")
                    }
                    rcpt_code = f"REC-{self.case['reference']}-{uuid.uuid4().hex[:8].upper()}"
                    cluster.receipt = rcpt_code
                    cluster.events.append({"time": datetime.now(timezone.utc).isoformat(), "action": "final_submit", "receipt": rcpt_code})

                return self._redirect(conf_paths[p])

            return False

        # --- Specification Origin Handlers ---
        def _handle_specification_get(self, path, query):
            p = self.profile
            docs_paths = {"classic": "/dokumente", "modern": "/specifications", "legacy": "/legacy_spec/docs.html"}
            rev_paths = {"classic": "/revisionen", "modern": "/revisions", "legacy": "/legacy_spec/revs.html"}
            req1_paths = {"classic": "/anforderungen/teil1", "modern": "/requirements/req-a", "legacy": "/legacy_spec/req1.html"}
            req2_paths = {"classic": "/anforderungen/teil2", "modern": "/requirements/req-b", "legacy": "/legacy_spec/req2.html"}
            decoy_paths = {"classic": "/alt-revision", "modern": "/decoy-revision", "legacy": "/legacy_spec/old.html"}

            if p == 'legacy' and path == "/frameset.html":
                content = f"""
                <h2>Technisches Dokumentationssystem (Legacy Frameset)</h2>
                <p>Dokumenteninhalt wird im nachfolgenden Frame gerendert:</p>
                <iframe src="/legacy_spec/docs.html" width="100%" height="500" style="border: 1px solid #666;"></iframe>
                """
                self._send_html(200, render_layout("Legacy Frameset", content, p, role, path, self._get_nav_links()))
                return True

            if path == docs_paths[p]:
                content = f"""
                <h2>Register der technischen Dokumente: {escape(self.case['reference'])}</h2>
                <p>Aktuelle Freigabeversion: <strong>{escape(self.case['revision'])}</strong></p>
                <p><a href="{rev_paths[p]}" class="btn">Revisionshistorie einsehen</a></p>
                <p><a href="{req1_paths[p]}" class="btn">Anforderungen anzeigen (Teil 1)</a></p>
                """
                self._send_html(200, render_layout("Technical Docs", content, p, role, path, self._get_nav_links()))
                return True

            elif path == rev_paths[p]:
                content = f"""
                <h2>Revisionshistorie</h2>
                <table>
                  <thead><tr><th>Revisionsstand</th><th>Status</th><th>Aktion</th></tr></thead>
                  <tbody>
                    <tr>
                      <td><strong>{escape(self.case['revision'])}</strong></td>
                      <td><span style="color: green; font-weight: bold;">Gültig / Current</span></td>
                      <td><a href="{req1_paths[p]}">Anforderungen öffnen</a></td>
                    </tr>
                    <tr>
                      <td>{escape(self.case['old_revision'])}</td>
                      <td><span style="color: red; font-weight: bold;">Überholt / Superseded</span></td>
                      <td><a href="{decoy_paths[p]}">Historische Version ansehen</a></td>
                    </tr>
                  </tbody>
                </table>
                <p><a href="{docs_paths[p]}" class="btn">Zurück zum Register</a></p>
                """
                self._send_html(200, render_layout("Revision History", content, p, role, path, self._get_nav_links()))
                return True

            elif path == req1_paths[p]:
                req_a = next((r for r in self.case['requirements'] if r['ref'] == 'REQ-A'), None)
                content = f"""
                <h2>Spezifikation {escape(self.case['revision'])}: Anforderung REQ-A (Teil 1 von 2)</h2>
                <p><a href="{rev_paths[p]}" class="btn">Revisionshistorie</a> &nbsp; <a href="{req2_paths[p]}" class="btn">Weiter zu REQ-B (Teil 2)</a></p>
                <table>
                  <tr><th>Parameter</th><th>Wert</th></tr>
                  <tr><td>Referenz</td><td>{escape(req_a['ref'])}</td></tr>
                  <tr><td>Kolbendurchmesser (bore_mm)</td><td>{escape(str(req_a['bore_mm']))} mm</td></tr>
                  <tr><td>Hub (stroke_mm)</td><td>{escape(str(req_a['stroke_mm']))} mm</td></tr>
                  <tr><td>Nenndruck (pressure_bar)</td><td>{escape(str(req_a['pressure_bar']))} bar</td></tr>
                  <tr><td>Befestigungsart (mounting)</td><td>{escape(req_a['mounting'])}</td></tr>
                  <tr><td>Zertifizierung (certification)</td><td>{escape(req_a['certification'])}</td></tr>
                  <tr><td>Kataloggruppe (catalog_group)</td><td>{escape(req_a['catalog_group'])}</td></tr>
                  <tr><td>Stück pro Satz (pieces_per_set)</td><td>{escape(str(req_a['pieces_per_set'])) + ' Stk pro Satz' if req_a['pieces_per_set'] is not None else 'Keine Satzumrechnung / Einzelteil'}</td></tr>
                </table>
                """
                self._send_html(200, render_layout("Requirement REQ-A", content, p, role, path, self._get_nav_links()))
                return True

            elif path == req2_paths[p]:
                req_b = next((r for r in self.case['requirements'] if r['ref'] == 'REQ-B'), None)
                content = f"""
                <h2>Spezifikation {escape(self.case['revision'])}: Anforderung REQ-B (Teil 2 von 2)</h2>
                <p><a href="{rev_paths[p]}" class="btn">Revisionshistorie</a> &nbsp; <a href="{req1_paths[p]}" class="btn">Zurück zu REQ-A (Teil 1)</a></p>
                <table>
                  <tr><th>Parameter</th><th>Wert</th></tr>
                  <tr><td>Referenz</td><td>{escape(req_b['ref'])}</td></tr>
                  <tr><td>Kolbendurchmesser (bore_mm)</td><td>{escape(str(req_b['bore_mm']))} mm</td></tr>
                  <tr><td>Hub (stroke_mm)</td><td>{escape(str(req_b['stroke_mm']))} mm</td></tr>
                  <tr><td>Nenndruck (pressure_bar)</td><td>{escape(str(req_b['pressure_bar']))} bar</td></tr>
                  <tr><td>Befestigungsart (mounting)</td><td>{escape(req_b['mounting'])}</td></tr>
                  <tr><td>Zertifizierung (certification)</td><td>{escape(req_b['certification'])}</td></tr>
                  <tr><td>Kataloggruppe (catalog_group)</td><td>{escape(req_b['catalog_group'])}</td></tr>
                  <tr><td>Stück pro Satz (pieces_per_set)</td><td>{escape(str(req_b['pieces_per_set'])) + ' Stk pro Satz' if req_b['pieces_per_set'] is not None else 'Keine Satzumrechnung / Einzelteil'}</td></tr>
                </table>
                """
                self._send_html(200, render_layout("Requirement REQ-B", content, p, role, path, self._get_nav_links()))
                return True

            elif path == decoy_paths[p]:
                content = f"""
                <h2>Historische Spezifikation {escape(self.case['old_revision'])} [ÜBERHOLT / DECOY]</h2>
                <div class="alert" style="border-color: red; background: #ffe6e6;">
                  <strong>WARNUNG:</strong> Diese Revision {escape(self.case['old_revision'])} ist ungültig und superseded!
                </div>
                <table>
                  <tr><th>Parameter</th><th>Historischer Wert</th></tr>
                  <tr><td>Referenz REQ-A Hub</td><td>135 mm (Abweichend!)</td></tr>
                  <tr><td>Referenz REQ-B Hub</td><td>240 mm (Abweichend!)</td></tr>
                </table>
                <p><a href="{rev_paths[p]}" class="btn">Zurück zur Revisionshistorie</a></p>
                """
                self._send_html(200, render_layout("Decoy Revision", content, p, role, path, self._get_nav_links()))
                return True

            return False

        # --- ERP Origin Handlers ---
        def _handle_erp_get(self, path, query):
            p = self.profile
            search_paths = {"classic": ["/katalog", "/katalog/suche"], "modern": ["/catalog", "/catalog/search"], "legacy": ["/erp_app/find.html"]}
            detail_base = {"classic": "/artikel", "modern": "/material", "legacy": "/erp_app/item.html"}

            if p == 'legacy' and path == "/frameset.html":
                content = f"""
                <h2>ERP Portal (Legacy Frameset)</h2>
                <iframe src="/erp_app/find.html" width="100%" height="600" style="border: 1px solid #666;"></iframe>
                """
                self._send_html(200, render_layout("ERP Frameset", content, p, role, path, self._get_nav_links()))
                return True

            if path in search_paths[p]:
                grp_query = query.get('group', query.get('gruppe', query.get('catalog_group', [''])))[0].strip()
                results_html = ""
                if grp_query:
                    candidates = [m for m in self.case['materials'] if m.get('catalog_group', '').upper() == grp_query.upper()]
                    if candidates:
                        rows = []
                        for mat in candidates:
                            m_num = mat['material_number']
                            url = f"{detail_base[p]}?id={m_num}" if p == 'legacy' else f"{detail_base[p]}/{m_num}"
                            status_txt = f"{mat['status_code']} ({mat['status_meaning']})"
                            rows.append(f"<tr><td><a href=\"{url}\"><strong>{escape(m_num)}</strong></a></td><td>{escape(mat['catalog_group'])}</td><td>{escape(mat['mounting'])}</td><td>{escape(status_txt)}</td><td>{escape(str(mat['unrestricted_stock']))}</td><td><a href=\"{url}\" class=\"btn\">Details</a></td></tr>")
                        results_html = f"""
                        <h3>Suchergebnisse für Gruppe '{escape(grp_query)}':</h3>
                        <table>
                          <thead><tr><th>Materialnummer</th><th>Kataloggruppe</th><th>Befestigung</th><th>Status</th><th>Freier Bestand</th><th>Aktion</th></tr></thead>
                          <tbody>{''.join(rows)}</tbody>
                        </table>
                        """
                    else:
                        results_html = f"<p>Keine Materialien für Gruppe '{escape(grp_query)}' gefunden.</p>"

                search_url = search_paths[p][0]
                content = f"""
                <h2>ERP Artikel-Katalogsuche</h2>
                <form method="GET" action="{search_url}">
                  <p><label>Kataloggruppe eingeben (z. B. CAT-A, CAT-B):<br>
                    <input type="text" name="group" value="{escape(grp_query)}" required>
                  </label></p>
                  <p><button type="submit" class="btn">Kataloggruppe suchen</button></p>
                </form>
                {results_html}
                <div class="legend">Status-Legende: R = active / released, B = blocked</div>
                """
                self._send_html(200, render_layout("Catalog Search", content, p, role, path, self._get_nav_links()))
                return True

            mat_id = None
            if p == 'legacy' and path == detail_base[p]:
                mat_id = query.get('id', query.get('nr', [None]))[0]
            elif path.startswith(detail_base[p]):
                rem = path[len(detail_base[p]):].strip("/")
                if rem:
                    mat_id = rem
                else:
                    mat_id = query.get('id', query.get('nr', [None]))[0]

            if mat_id:
                mat = next((m for m in self.case['materials'] if m['material_number'] == mat_id), None)
                if mat:
                    cond_rows = []
                    for cond in mat.get('conditions', []):
                        scales_html = "<br>".join([f"ab {s['from_qty']} Stk: {s['price']} {cond['currency']}" for s in cond.get('scales', [])])
                        cond_rows.append(f"""
                        <tr>
                          <td>{escape(cond.get('record', ''))}</td>
                          <td><time datetime="{cond['valid_from']}">{cond['valid_from']}</time> bis <time datetime="{cond['valid_to']}">{cond['valid_to']}</time></td>
                          <td>{escape(cond['currency'])}</td>
                          <td>{escape(str(cond['price_unit_qty']))}</td>
                          <td>{escape(str(cond.get('fx_key') or 'kein FX'))}</td>
                          <td>{escape(cond.get('number_format', ''))}</td>
                          <td>{scales_html}</td>
                        </tr>
                        """)

                    back_url = search_paths[p][0]
                    content = f"""
                    <h2>Materialdetail: {escape(mat['material_number'])}</h2>
                    <p><a href="{back_url}?group={escape(mat['catalog_group'])}" class="btn">Zurück zur Suche</a></p>

                    <h3>1. Technische Spezifikation</h3>
                    <table>
                      <tr><th>Kataloggruppe</th><td>{escape(mat['catalog_group'])}</td></tr>
                      <tr><th>Abmessungen (Bore x Stroke x Pressure)</th><td>{escape(str(mat['bore_mm']))} mm x {escape(str(mat['stroke_mm']))} mm @ {escape(str(mat['pressure_bar']))} bar</td></tr>
                      <tr><th>Befestigungsart</th><td>{escape(mat['mounting'])}</td></tr>
                      <tr><th>Zertifizierung</th><td>{escape(mat['certification'])}</td></tr>
                      <tr><th>Status</th><td><strong>{escape(mat['status_code'])} ({escape(mat['status_meaning'])})</strong></td></tr>
                    </table>
                    <div class="legend">Status-Legende: R = active / released, B = blocked</div>

                    <h3>2. Bestände & Logistik</h3>
                    <table>
                      <tr><th>Frei verfügbarer Bestand (unrestricted_stock)</th><td>{escape(str(mat['unrestricted_stock']))} Stk</td></tr>
                      <tr><th>Qualitätsprüfbestand (inspection_stock)</th><td>{escape(str(mat['inspection_stock']))} Stk</td></tr>
                      <tr><th>Gesperrter Bestand (blocked_stock)</th><td>{escape(str(mat['blocked_stock']))} Stk</td></tr>
                      <tr><th>Lieferzeit ab Lager (lead_time_stock_weeks)</th><td>{escape(str(mat['lead_time_stock_weeks']))} Wochen</td></tr>
                      <tr><th>Lieferzeit Neufertigung (lead_time_production_weeks)</th><td>{escape(str(mat['lead_time_production_weeks']))} Wochen</td></tr>
                      <tr><th>Verpackungseinheit (pieces_per_carton)</th><td>{escape(str(mat['pieces_per_carton']))} Stk/Karton</td></tr>
                      <tr><th>Zuschlagsgewicht (surcharge_mass_g)</th><td>{escape(str(mat['surcharge_mass_g']))} g</td></tr>
                      <tr><th>Metallpreisindex (price_index_key)</th><td>{escape(str(mat.get('price_index_key') or 'keiner'))}</td></tr>
                      <tr><th>Allokationsgewicht (allocation_weight_g)</th><td>{escape(str(mat['allocation_weight_g']))} g</td></tr>
                    </table>

                    <h3>3. Konditionen & Preisstaffeln</h3>
                    <table>
                      <thead><tr><th>Status</th><th>Gültigkeit</th><th>Währung</th><th>Preiseinheit</th><th>FX-Schlüssel</th><th>Format</th><th>Staffeln</th></tr></thead>
                      <tbody>{''.join(cond_rows)}</tbody>
                    </table>
                    """
                    self._send_html(200, render_layout(f"Detail {mat['material_number']}", content, p, role, path, self._get_nav_links()))
                    return True

            return False

        # --- Mail Origin Handlers ---
        def _handle_mail_get(self, path, query):
            p = self.profile
            inbox_paths = {"classic": "/postfach/posteingang", "modern": "/inbox", "legacy": "/webmail/inbox.html"}
            archive_paths = {"classic": "/postfach/archiv", "modern": "/archive", "legacy": "/webmail/archive.html"}
            msg_base = {"classic": "/nachricht", "modern": "/message", "legacy": "/webmail/read.html"}

            target_folder = None
            if path == inbox_paths[p]:
                target_folder = "inbox"
            elif path == archive_paths[p]:
                target_folder = "archive"

            if target_folder:
                q_raw = query.get('q', [''])[0].strip()
                terms = q_raw.lower().split()

                with cluster.lock:
                    all_msgs = copy.deepcopy(cluster.messages)

                folder_msgs = [m for m in all_msgs if m.get('folder') == target_folder]
                if terms:
                    matched = []
                    for m in folder_msgs:
                        haystack = f"{m.get('subject', '')} {m.get('body', '')} {m.get('sender', '')}".lower()
                        if all(t in haystack for t in terms):
                            matched.append(m)
                    folder_msgs = matched

                page_str = query.get('page', query.get('seite', ['1']))[0]
                try:
                    page = max(1, int(page_str))
                except ValueError:
                    page = 1
                per_page = 3
                total_pages = max(1, math.ceil(len(folder_msgs) / per_page))
                if page > total_pages:
                    page = total_pages
                start_idx = (page - 1) * per_page
                page_msgs = folder_msgs[start_idx:start_idx + per_page]

                rows = []
                for m in page_msgs:
                    m_id = m.get('id', '')
                    read_url = f"{msg_base[p]}?id={m_id}" if p == 'legacy' else f"{msg_base[p]}/{m_id}"
                    dt = m.get('datetime_utc', '')
                    rows.append(f"<tr><td>{escape(m.get('sender', ''))}</td><td><time datetime=\"{dt}\">{dt}</time></td><td><a href=\"{read_url}\"><strong>{escape(m.get('subject', ''))}</strong></a></td></tr>")

                curr_url = inbox_paths[p] if target_folder == 'inbox' else archive_paths[p]
                page_links = []
                param_name = 'seite' if p == 'classic' else 'page'
                q_param = f"&q={unquote(q_raw)}" if q_raw else ""
                if page > 1:
                    page_links.append(f'<a href="{curr_url}?{param_name}={page - 1}{q_param}" class="btn">Vorherige</a>')
                if page < total_pages:
                    page_links.append(f'<a href="{curr_url}?{param_name}={page + 1}{q_param}" class="btn">Nächste</a>')

                folder_title = "Posteingang (Inbox)" if target_folder == 'inbox' else "Archiv (Archive)"
                content = f"""
                <h2>{folder_title} - Mailbox: {escape(self.case['mailbox'])}</h2>
                <form method="GET" action="{curr_url}" style="margin-bottom: 16px;">
                  <label>Nachrichten durchsuchen (AND-Suche): <input type="text" name="q" value="{escape(q_raw)}"></label>
                  <button type="submit" class="btn">Suchen</button>
                  {f'<a href="{curr_url}" style="margin-left: 8px;">Zurücksetzen</a>' if q_raw else ''}
                </form>
                <table>
                  <thead><tr><th>Absender</th><th>Datum</th><th>Betreff</th></tr></thead>
                  <tbody>{''.join(rows) if rows else '<tr><td colspan="3">Keine Nachrichten gefunden.</td></tr>'}</tbody>
                </table>
                <div style="margin: 15px 0;">{' &nbsp; '.join(page_links)}</div>
                """
                self._send_html(200, render_layout(folder_title, content, p, role, path, self._get_nav_links()))
                return True

            msg_id = None
            if p == 'legacy' and path == msg_base[p]:
                msg_id = query.get('id', [None])[0]
            elif path.startswith(msg_base[p]):
                rem = path[len(msg_base[p]):].strip("/")
                if rem:
                    msg_id = rem
                else:
                    msg_id = query.get('id', [None])[0]

            if msg_id:
                with cluster.lock:
                    found_msg = next((m for m in cluster.messages if str(m.get('id')) == str(msg_id)), None)
                if found_msg:
                    dt = found_msg.get('datetime_utc', '')
                    f_name = found_msg.get('folder', 'inbox')
                    back_url = inbox_paths[p] if f_name == 'inbox' else archive_paths[p]
                    content = f"""
                    <h2>{escape(found_msg.get('subject', ''))}</h2>
                    <p><a href="{back_url}" class="btn">Zurück zum Ordner</a></p>
                    <div style="background: #fdfdfd; border: 1px solid #ccc; padding: 12px; margin-bottom: 15px;">
                      <p><strong>Absender:</strong> {escape(found_msg.get('sender', ''))}</p>
                      <p><strong>Empfänger-Mailbox:</strong> {escape(self.case['mailbox'])}</p>
                      <p><strong>Datum:</strong> <time datetime="{dt}">{dt}</time></p>
                      <p><strong>Ordner:</strong> {escape(f_name)}</p>
                    </div>
                    <h3>Nachrichtentext (Original):</h3>
                    <pre style="background: #f5f5f5; border: 1px solid #ddd; padding: 15px; white-space: pre-wrap; font-family: monospace;">{escape(found_msg.get('body', ''))}</pre>
                    """
                    self._send_html(200, render_layout("Message View", content, p, role, path, self._get_nav_links()))
                    return True

            return False

        def _handle_mail_post(self, path, form):
            return False

    return RoleHandler

class PortalCluster:
    def __init__(self, case_name='baseline', profile='classic', base_port=0):
        cases = load_fixture_cases()
        if case_name not in cases:
            raise ValueError(f"Unknown case: {case_name}")
        if profile not in ('classic', 'modern', 'legacy'):
            raise ValueError(f"Unknown profile: {profile}")
        self.case_name = case_name
        self.profile = profile
        self.case = copy.deepcopy(cases[case_name])
        self.base_port = base_port
        self.lock = threading.RLock()
        self.servers = {}
        self.threads = {}
        self.origins = {}
        self._started = False
        self._stopped = False
        self.submitted = None
        self.receipt = None
        self.tan_requests = 0
        self.events = []
        self.draft = None
        self.active_tans = {}
        self.sessions = {r: set() for r in ('tender', 'specification', 'erp', 'mail')}
        self.messages = copy.deepcopy(self.case.get('messages', []))

    def start(self):
        with self.lock:
            if self._started:
                raise RuntimeError("Cluster already started")
            roles = ('tender', 'specification', 'erp', 'mail')
            for idx, role in enumerate(roles):
                port = (self.base_port + idx) if self.base_port > 0 else 0
                handler_cls = make_handler_class(self, role)
                server = ThreadingHTTPServer(('127.0.0.1', port), handler_cls)
                actual_port = server.server_port
                self.servers[role] = server
                self.origins[role] = f"http://127.0.0.1:{actual_port}"
                t = threading.Thread(target=server.serve_forever, daemon=True)
                self.threads[role] = t
                t.start()
            self._started = True
            return self

    def snapshot(self):
        with self.lock:
            return {
                "submitted": copy.deepcopy(self.submitted),
                "receipt": self.receipt,
                "tan_requests": self.tan_requests,
                "events": copy.deepcopy(self.events)
            }

    def stop(self):
        with self.lock:
            if not self._started or self._stopped:
                return
            self._stopped = True
            servers, threads = list(self.servers.values()), list(self.threads.values())
            self.servers.clear()
            self.threads.clear()
        # Handlers may need the cluster lock while finishing an in-flight request.
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2.0)

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Mini RFQ Portal Cluster")
    parser.add_argument('--profile', default='classic', choices=['classic', 'modern', 'legacy'])
    parser.add_argument('--case', default='baseline')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    cluster = PortalCluster(case_name=args.case, profile=args.profile, base_port=args.port)
    cluster.start()
    print(json.dumps(cluster.origins))
    sys.stdout.flush()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        cluster.stop()
