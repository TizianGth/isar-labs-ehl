# 05 · Browser-Playbook (Playwright über CDP)

## Verbindung

```python
urls = json.loads(os.environ.get("CDP_URLS") or "[]") or ([os.environ["CDP_URL"]] if os.environ.get("CDP_URL") else [])
if urls:   # Sandbox: NIE eigene Browser starten
    browser = await pw.chromium.connect_over_cdp(urls[i])
    context = browser.contexts[0] if browser.contexts else await browser.new_context()
else:      # lokal
    browser = await pw.chromium.launch(headless=os.environ.get("HEADLESS") == "1")
    context = await browser.new_context(viewport={"width": 1280, "height": 800}, locale="de-DE")
```

- `agent.json`: `"browsers": 4` → ein Browser je Site (Sessions/Cookies sauber getrennt, echte Parallelität).
- Pro Site eine `SiteSession` (page, credentials, base_url, last_good_url, login_state). Weitere Tabs nur bei Bedarf.
- Viewport ist 1280×800 — Screenshots fallen klein aus; DOM-Lesen ist primär.

## Pflicht-Handler pro Page/Context (in `browser/session.py`)

```python
page.on("dialog", on_dialog)          # native alert/confirm/prompt: Default = dismiss + loggen;
                                      # NUR die finale Abgabe setzt vorher page.once("dialog", accept)
context.on("page", on_new_page)       # Popups (window.open, target=_blank) einsammeln → Explorer informieren
page.on("response", on_response)      # 5xx zählen → Guard-Retry
page.set_default_timeout(15_000); page.set_default_navigation_timeout(30_000)
```

Playwright verwirft native Dialoge automatisch, wenn kein Handler existiert — die finale Abgabe würde still
scheitern. Beim Abschicken: `page.once("dialog", lambda d: asyncio.create_task(d.accept()))` **vor** dem Klick.

## Guards (vor/nach jeder Aktion, Code — kein LLM nötig, LLM-Fallback bei Unklarheit)

| Guard | Erkennung (generisch) | Aktion |
| --- | --- | --- |
| `close_overlays` | `dialog[open]`, `[role=dialog][aria-modal=true]`, sichtbare Overlays mit hohem z-index, die Klicks abfangen | Button mit `aria-label`/Text „Schließen“, „×“, „Close“, „Später“, „Nein danke“ klicken. **Nie** „Akzeptieren und fortfahren“, „Bewerten“, „Zur Umfrage“. Escape ist oft blockiert. |
| `consent` | Seite/Overlay mit „Cookie“, „Einwilligung“, „Datenschutz“ + Buttons | „Notwendige …/Nur notwendige“ bevorzugen, sonst „Akzeptieren“ |
| `login` | sichtbares Passwortfeld + Benutzername-Feld | Credentials aus Run eintragen, absenden, danach `returnTo`/letzte URL wiederherstellen; Zähler (max 5/Session) |
| `error_retry` | HTTP ≥ 500, Texte „vorübergehend nicht verfügbar“, „Fehler“, „erneut versuchen“, „Laden fehlgeschlagen“, leere Seite | „Erneut versuchen“-Link/Reload, Backoff 0.5/1/2/4 s |
| `unlock_gate` | `input[type=range]` + Checkbox „kein Roboter“ + `fieldset[disabled]`/`[inert]` | `locator.fill("100")` auf Range (setzt Wert + `input`-Event), Checkbox an, Status prüfen („Entsperrt“), dann LLM füllt den Rest (Belegnummer etc.) |
| `settle` | nach Navigation/Klick | `wait_for_load_state("domcontentloaded")` + max 1,5 s auf DOM-Ruhe (MutationObserver) — verzögerte Banner/Dialoge (650–1100 ms) abwarten |

Guards laufen auch **innerhalb von iframes** (Overlays können im Frame sitzen).

## Observation: Serializer-Spezifikation (`browser/observe.js`, läuft in jedem Frame)

Ziel: kompakter, fallensicherer Text, den ein LLM ohne Screenshot versteht. Ausgabe je Frame mit Kopfzeile
`=== frame <n> <url> ===`.

**Einschließen**
- Überschriften, Absätze, Listen, Tabellen (Zeilen als `| a | b |` mit Kopfzeile), Formularfelder mit Label, Wert,
  Optionen (bei `select` alle Optionen + selected), `disabled`, `required`, `checked`.
- Interaktive Elemente mit Ref: `[e17] link "Öffnen" → /tenders/…/documents/2`, `[e18] button "Weiter"`.
  Ref = `data-agent-ref`-Attribut, das der Serializer setzt (stabil bis zur nächsten Navigation).
- `aria-label` von `role=img`, `canvas`, `svg[role=img]`, `img[alt]`, `output[aria-label]`, `[role=group][aria-label]`
  (enthält oft den sauberen Wert inkl. Locale-Tag).
- **Tooltips**: Element mit `aria-describedby` → Text des referenzierten Elements als `[desc: …]`, auch wenn es
  `display:none` ist. Ebenso `title`-Attribute.
- **Geschlossene `<details>`**: Inhalt ausgeben, markiert als `(eingeklappt)`; `summary` als Überschrift.
- `<time datetime>` → beide Darstellungen.
- `role=listitem` mit `aria-posinset` bzw. Flex-`order` → in logischer Reihenfolge ausgeben.
- `data-label`, das über CSS `::before` angezeigt wird.
- Status-/Live-Regionen (`role=status`, `aria-live`) — enthalten Fehlermeldungen wie „Funktion nicht verfügbar“.

**Ausschließen**
- Teilbäume mit `aria-hidden="true"`, `hidden`, `inert` (aber Hinweis „[gesperrt]“ für `fieldset[disabled]` mit Inhalt),
  `display:none`/`visibility:hidden` — **außer** Tooltips (s. o.) und `details`-Inhalt.
- `script`, `style`, `noscript`, Templates, SVG-Pfaddaten.
- Fake-Browser-Chrome nicht löschen, aber als Region markiert lassen (LLM-Prompt sagt: ignorieren).

**Normalisieren**
- Zero-Width & Soft-Hyphen entfernen (`[​-‍⁠﻿­]`), Whitespace kollabieren, NBSP → Space.
- Wiederholte Boilerplate (Footer, Header) nur einmal pro Seite.

**Größe**: Ziel < 6 k Tokens pro Observation; bei Überlänge Abschnitte mit `[… N Zeilen gekürzt, observe(scope=…)]`.
Diff-Modus: nur Änderungen seit letzter Observation (spart Tokens bei Formular-Schritten).

**Vision-Fallback**: `page.screenshot()` (JPEG, Qualität 60) nur, wenn ein relevantes Canvas/Bild **kein**
`aria-label` hat oder das LLM es anfordert. Qwen ist multimodal.

## Aktionen (`browser/actions.py`)

- Immer über Ref-Locator `[data-agent-ref="e17"]` im richtigen Frame; vor Aktion `close_overlays`, nach Aktion `settle`
  + Kurz-Observation (Diff) zurückgeben.
- `click`: `locator.click(timeout=…)`; bei „intercepts pointer events“ → Overlay-Guard, dann erneut; letzter Ausweg
  `locator.dispatch_event("click")`. Bei Links mit `href` kann `open_link` direkt `page.goto(abs_url)` nutzen
  (umgeht `window.open`-Popups; vom Modell gewählter Link ⇒ erlaubt).
- `fill`: `locator.fill(text)` → Wert zurücklesen und vergleichen.
- `select`: per `value` oder Label; danach zurücklesen. Achtung **vorausgewählte Fallen-Optionen**.
- `check`: `set_checked(True/False)`.
- `set_range`: `fill(str(value))` (funktioniert für `input[type=range]` und feuert `input`/`change`).
- `hover`/`expand`: Menüs mit `:hover`/`:focus-within`, `details` via `evaluate("e => e.open = true")`.
- `read_document`: `context.request.get(url)` nutzt die Session-Cookies; Content-Type → CSV/TXT/HTML/ZIP (zipfile,
  UTF-8/CP437-Dateinamen)/PDF (pypdf) → Text. Bei HTML denselben Serializer anwenden.
- `paginate(ref_next)`: folgt „Nächste Seite“-Links bis keine mehr / Seitenzahl erreicht / Duplikat erkannt;
  gibt alle Seiten-Observations konkateniert zurück. „Weitere laden“ (fetch) nur als Fallback.

## Sessions & Zustand

- Login-Zustand pro Site prüfen, bevor eine Mission startet. Nach Relogin landet man ggf. auf Start/Cookie/Gate →
  Guards erneut.
- Konfigurator: **jeder Schritt muss mit „Weiter“ gespeichert werden**; „Zurück“ verwirft Folgeschritte; Session-
  Timeout verwirft Ungespeichertes → zügig arbeiten, alle Werte vorher fertig berechnet haben.
- TAN-Fenster 5 min, neue Anforderung invalidiert alte → genau einmal anfordern, dann sofort lesen & abgeben.

## Zahlen, Daten, Zeitzonen (`rules/parse.py`)

| Eingabe | Ergebnis |
| --- | --- |
| `2.743,58 (de-DE)` | `Decimal("2743.58")` |
| `238,098 (en-US)` | `Decimal("238098")` |
| `30,851.65 (en-US)` | `Decimal("30851.65")` |
| `13.25` + Kontext „Dezimalpunkt, keine Tausendertrennzeichen“ | `Decimal("13.25")` |
| `1.234` ohne Locale | **mehrdeutig** → Kontext/LLM entscheiden lassen, loggen |
| `05/01 (TT/MM) 2027` | 2027-01-05 |
| `01/17 (MM/TT) 2027` | 2027-01-17 |
| `25.01.2027, 10:00` + Seite „Zeitzone: Europe/Athens“ | 2027-01-25T08:00Z |
| `<time datetime="2027-01-24T16:00:00Z">` | direkt |

Immer `zoneinfo`; ohne Zeitangabe: Datum der Site-Zeitzone, Vergleich mit Stichtag auf Kalendertag-Basis in derselben
Zone (inklusive Grenzen). Einheiten: `Stk/ST/Stück` = Stück; `Satz`/`Set`/`Paar` → Umrechnung aus Spezifikation.

## Performance

- 4 Browser parallel, je Site ein Explorer; LLM-Calls asynchron (Limit Qwen 32 parallel/Team inkl. Teammitglieder-
  Test-Runs!).
- `wait_for_timeout` nur gezielt (Banner/Dialog-Delays), sonst ereignisbasiert warten.
- Dokumente/Listen per `context.request` holen statt durchklicken, **nachdem** das Modell sie ausgewählt hat.
