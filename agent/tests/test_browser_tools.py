"""B01/B04/B05 on a synthetic website served through Playwright routing (no network, no model)."""
import asyncio
import re
from collections import Counter

from playwright.async_api import async_playwright

from rfq import llm
from rfq.agent.explorer import Explorer, Mission, _hints
from rfq.browser.site import Site

BASE = "http://shop.test"


def _list_page(n: int) -> str:
    nxt = f'<a href="/list?page={n + 1}">Nächste Seite</a>' if n < 4 else ""
    extra = '<a href="/doc/spec">Lastenheft</a>' if n == 3 else ""
    return f"<html><body><h1>Register</h1><p>Eintrag {n}</p>{extra}<p>Seite {n} von 4</p>{nxt}</body></html>"


PAGES = {
    "/": "<html><body><h1>Start</h1><a href='/list?page=1'>Register</a></body></html>",
    "/doc/spec": "<html><body><h1>Lastenheft</h1></body></html>",
    "/tools": """<html><body>
      <style>#menu ul{display:none} #menu:hover ul{display:block}</style>
      <nav id="menu"><button>Menü</button><ul><li><a href="/doc/spec">Lastenheft</a></li></ul></nav>
      <select id="s" disabled><option>a</option><option>b</option></select>
      <script>setTimeout(() => document.getElementById('s').disabled = false, 400)</script>
      <div id="box" style="height:60px;overflow:auto"><div style="height:600px">lang</div></div>
      <canvas id="c" width="200" height="40"></canvas></body></html>""",
}


async def _site(p, hits: Counter):
    browser = await p.chromium.launch()
    context = await browser.new_context()

    async def handle(route):
        path = re.sub(r"^https?://[^/]+", "", route.request.url)
        hits[path] += 1
        if path.startswith("/list"):
            body = _list_page(int(path.rsplit("=", 1)[1]))
        else:
            body = PAGES.get(path, "<html><body>404</body></html>")
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

    await context.route(f"{BASE}/**", handle)
    site = Site("shop", BASE, {"username": "u", "password": "p"}, context)
    await site.start()
    return browser, site


def _ref(observation: str, label: str) -> str:
    return re.search(r'\[(\w+)\] (?:link|button) "' + re.escape(label), observation).group(1)


def test_collection_offers_links_and_is_not_reloaded():
    calls = []

    async def extractor(kind, text, url, via="page"):
        calls.append((kind, url, via))
        return "ok"

    async def scenario():
        hits = Counter()
        async with async_playwright() as p:
            browser, site = await _site(p, hits)
            await site.goto(f"{BASE}/list?page=1")
            explorer = Explorer(site, Mission("t", "goal", {}), extractor)
            ref = _ref(await site.observe(), "Nächste Seite")
            first = await explorer._read_all_pages(ref, None, 10)
            loads = sum(v for k, v in hits.items() if k.startswith("/list"))
            ref = _ref(await site.observe(), "Nächste Seite")
            second = await explorer._read_all_pages(ref, "positions", 10)
            reloads = sum(v for k, v in hits.items() if k.startswith("/list")) - loads
            await browser.close()
            return first, second, reloads
    first, second, reloads = asyncio.run(scenario())
    assert "4 pages" in first and re.search(r"Lastenheft( \[[^\]]*\])? -> http://shop.test/doc/spec", first)
    assert "not extracted yet" in first
    assert "already collected" in second and reloads == 0
    assert [c[1] for c in calls] == [f"{BASE}/list?page={n}" for n in range(1, 5)]
    assert all(c[2] == "collection" for c in calls)


def test_hover_wait_scroll_and_visual_fallback(monkeypatch):
    seen_parts = []

    async def fake_ask_json(system, user, **kwargs):
        seen_parts.append(user)
        return {"answer": "Belegnummer 4711", "uncertain": None}

    monkeypatch.setattr(llm, "ask_json", fake_ask_json)

    async def scenario():
        async with async_playwright() as p:
            browser, site = await _site(p, Counter())
            await site.goto(f"{BASE}/tools")
            observation = await site.observe()
            hovered = await site.act("hover", {"ref": _ref(observation, "Menü")})
            link_visible = await site.page.locator("#menu a").is_visible()
            observation = await site.observe()
            select_ref = re.search(r'\[(\w+)\] select', observation).group(1)
            waited = await site.act("wait_for", {"condition": "enabled", "ref": select_ref, "timeout_s": 3})
            box_ref = await site.page.evaluate("() => { const e = document.getElementById('box'); e.setAttribute('data-agent-ref', 'e900'); return 'e900'; }")
            scrolled = await site.act("scroll", {"ref": box_ref, "direction": "down", "amount": 200})
            hint = _hints(await site.observe(), True)
            explorer = Explorer(site, Mission("t", "goal", {}, deadline=10**10), None)
            visual = await explorer._inspect_visual("Was steht im Bild?", None)
            text = await site.full_text()
            await browser.close()
            return hovered, link_visible, waited, scrolled, hint, visual, text
    hovered, link_visible, waited, scrolled, hint, visual, text = asyncio.run(scenario())
    assert "hovering" in hovered and link_visible
    assert "met" in waited
    assert "scrolled the element by 200px" in scrolled
    assert "inspect_visual" in hint
    assert "4711" in visual and "Belegnummer 4711" in text and "(visual)" in text
    assert any(isinstance(u, list) and any(part.get("type") == "image_url" for part in u) for u in seen_parts)


def test_a_frozen_page_never_blocks_the_agent(monkeypatch):
    """Regression (sandbox runs run_1a09…, run_f33b…): a tab whose JS never returns must be replaced, not waited for."""
    import time
    import rfq.browser.site as site_module
    monkeypatch.setattr(site_module, "EVAL_TIMEOUT", 1)

    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context()

            async def handle(route):
                await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                    body="<html><body><p>busy</p></body></html>")

            await context.route(f"{BASE}/**", handle)
            site = Site("frozen", BASE, {"username": "u", "password": "p"}, context)
            await asyncio.wait_for(site.start(), timeout=40)
            await site.page.evaluate("setTimeout(() => { while (true) {} }, 10)")
            await asyncio.sleep(0.3)                      # now this tab's JS never returns (a fresh tab is fine)
            frozen = site.page
            started = time.time()
            observation = await asyncio.wait_for(site.observe(), timeout=40)
            took = time.time() - started
            replaced = site.page is not frozen and site._recoveries == 1
            await browser.close()
            return observation, took, replaced
    observation, took, replaced = asyncio.run(scenario())
    # the hung tab is detected and replaced by a fresh tab at the last good address
    assert replaced and took < 20 and "busy" in observation


def test_hanging_page_falls_back_to_load_more(monkeypatch):
    """Regression (sandbox run_c8fe…): one pagination page never loads; the list's 'load more' control is used."""
    import rfq.browser.site as site_module
    monkeypatch.setattr(site_module, "NAVIGATION_TIMEOUT_MS", 1500)
    monkeypatch.setattr(site_module, "LOADED_TIMEOUT_MS", 1500)
    never = asyncio.Event()

    def page(n: int) -> str:
        return (f"<html><body><ul id='results'><li>Eintrag {n}</li></ul><p>Seite {n} von 4</p>"
                f"<a href='/list?page={n + 1}'>Nächste Seite</a>"
                f"<button id='more' onclick=\"fetch('/rows?page='+(window.nx=(window.nx||{n})+1)).then(r=>r.text())"
                f".then(t=>{{document.getElementById('results').insertAdjacentHTML('beforeend',t);"
                f"if(window.nx>=4)document.getElementById('more').remove();}})\">Weitere laden</button></body></html>")

    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context()

            async def handle(route):
                path = re.sub(r"^https?://[^/]+", "", route.request.url)
                if path == "/list?page=3":                # this page never answers (request stays open)
                    async def later(pending=route):
                        await never.wait()
                    asyncio.ensure_future(later())
                    return
                if path.startswith("/rows"):
                    n = int(path.rsplit("=", 1)[1])
                    body = f"<li>Eintrag {n}</li>"
                else:
                    body = page(int(path.rsplit("=", 1)[1])) if path.startswith("/list") else "<html><body>Start</body></html>"
                await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

            await context.route(f"{BASE}/**", handle)
            site = Site("shop", BASE, {"username": "u", "password": "p"}, context)
            await site.start()
            await site.goto(f"{BASE}/list?page=1")
            explorer = Explorer(site, Mission("t", "goal", {}), None)
            result = await explorer._read_all_pages(_ref(await site.observe(), "Nächste Seite"), None, 10)
            collection = list(site.collections.values())[-1]
            texts = "\n".join(text for _, text in collection.pages)
            never.set()
            await browser.close()
            return result, texts
    result, texts = asyncio.run(scenario())
    assert "did not load" in result and "load more" in result
    assert "Eintrag 3" in texts and "Eintrag 4" in texts


def test_decisive_steps_get_a_fresh_start_on_a_site():
    """Run dc33…: the mail start page hung twice during research, so the TAN step skipped it for good."""
    site = Site("mail", BASE, {"username": "u", "password": "p"}, None)
    site.hung[BASE] = 2
    site._recoveries = 8
    site.collections["c1"] = object()          # an inbox list collected before the TAN mail arrived
    site.fresh_start()
    assert site.hung == {} and site._recoveries == 0 and site.collections == {}


def test_next_page_link_inside_a_table_row():
    """Pagers in table rows hold several links in one line; the next page must still be found."""
    from rfq.agent.explorer import _next_link
    row = ('| Seite 2 von 3 ; [e6] link "Vorherige Seite" -> /qa?page=1 ; [e7] link "Nächste Seite" -> /qa?page=3 ; '
           '[e8] link "Drucken" -> /qa/print |')
    assert _next_link(row, "Nächste Seite") == "/qa?page=3"
    assert _next_link('[e1] link "Nächste Seite" -> /next', "Nächste Seite") == "/next"
    assert _next_link('| [e6] link "Vorherige Seite" -> /qa?page=2 |', "Nächste Seite") is None   # the last page


def test_a_click_folds_only_its_own_popup(monkeypatch):
    """Another mission's tab in the same browser context is neither closed nor loaded into our tab."""
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context()

            async def handle(route):
                url = route.request.url
                if url.endswith("/opener"):
                    body = ("<html><body><p>Start page of the test portal</p><button onclick=\"window.open('/target')\">Open</button>"
                            "<button onclick=\"window.otherMission()\">Mark</button></body></html>")
                elif url.endswith("/target"):
                    body = "<html><body><p>Target window with its own readable content</p></body></html>"
                else:
                    body = "<html><body><p>A page another mission works on right now</p></body></html>"
                await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

            await context.route(f"{BASE}/**", handle)
            site = Site("popups", BASE + "/opener", {"username": "u", "password": "p"}, context)
            await asyncio.wait_for(site.start(), timeout=40)
            others = []

            async def other_mission():          # another mission opens a tab exactly while our click is handled
                others.append(await context.new_page())
                await others[0].goto(BASE + "/elsewhere")

            await site.page.expose_function("otherMission", other_mission)
            # 1) our click opens nothing while another mission opens a tab: that tab is not ours to fold
            marked = await site.act("click", {"ref": _ref(await site.observe(), "Mark")})
            await asyncio.sleep(0.5)
            other = others[0]
            first = (marked, other.is_closed(), site.page.url)
            # 2) our click opens a window: it is loaded in our tab
            opened = await site.act("click", {"ref": _ref(await site.observe(), "Open")})
            state = (first, opened, site.page.url, other.is_closed())
            await browser.close()
            return state
    (marked, other_closed, url_after_mark), opened, url_after_open, other_closed_later = asyncio.run(scenario())
    assert marked == "clicked" and not other_closed and url_after_mark.endswith("/opener")
    assert "popup window opened" in opened and url_after_open.endswith("/target") and not other_closed_later


def test_vision_targets_the_document_and_never_looks_twice(monkeypatch):
    """Scanned documents (canvas) get a ref; a tab button is not the document; an unchanged picture is not looked at
    again (fake-portal 'scanned': the budget went to a tab button and to repeated, cut-off views)."""
    calls = []

    async def fake_ask_json(system, user, **kwargs):
        calls.append(user)
        return {"answer": "Preis 12,50", "uncertain": None}

    monkeypatch.setattr(llm, "ask_json", fake_ask_json)
    page = ("<html><body><p>Technisches Datenblatt</p><button>Preise</button>"
            "<canvas id='doc' width='600' height='400'></canvas>"
            "<script>const c = document.getElementById('doc').getContext('2d'); c.fillStyle = '#fff';"
            "c.fillRect(0, 0, 600, 400); c.fillStyle = '#000'; c.font = '30px sans-serif'; c.fillText('Preis 12,50', 40, 80);"
            "</script><img src='data:image/gif;base64,R0lGODlhAQABAAAAACw=' width='16' height='16'></body></html>")

    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context()

            async def handle(route):
                await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=page)

            await context.route(f"{BASE}/**", handle)
            site = Site("scan", BASE + "/doc", {"username": "u", "password": "p"}, context)
            await asyncio.wait_for(site.start(), timeout=40)
            observation = await site.observe()
            explorer = Explorer(site, Mission("t", "goal", {}, deadline=10**10), None)
            first = await explorer._inspect_visual("Welcher Preis steht im Dokument?", _ref(observation, "Preise"))
            again = await explorer._inspect_visual("Und nochmal?", _ref(observation, "Preise"))
            untargeted = await explorer._inspect_visual("Und ohne Ziel?", None)    # same picture: not looked at again
            await browser.close()
            return observation, first, again, explorer.visual_used, untargeted
    observation, first, again, used, untargeted = asyncio.run(scenario())
    assert untargeted.startswith("ERROR: this screenshot is identical")
    assert re.search(r"\[\w+\] canvas without text: needs screenshot \(600x400 px\)", observation)
    assert not re.search(r"\[\w+\] image without text", observation)       # a 16 px icon is no document
    assert "largest picture" in first and "12,50" in first
    assert again.startswith("ERROR: this screenshot is identical") and used == 1 and len(calls) == 1


CASCADE = """<html><body><form id="f"></form><script>
const state = {bore: "", extra: false, stroke: "", mount: "", level: 1};
function render() {
  const opt = (vals, sel) => vals.map((v) => `<option${v === sel ? " selected" : ""}>${v}</option>`).join("");
  document.getElementById("f").innerHTML =
    `<label>Bohrung <select name="bore">${opt(["", "40", "50"], state.bore)}</select></label>` +
    (state.extra ? `<label>Variante <select name="variant"><option></option><option>A</option></select></label>` : "") +
    `<label>Hub <select name="stroke" ${state.level < 2 ? "disabled" : ""}>${opt(["", "500", "600"], state.stroke)}</select></label>` +
    `<label>Befestigung <select name="mount" ${state.level < 3 ? "disabled" : ""}>${opt(["", "Flansch", "Fuss"], state.mount)}</select></label>` +
    `<button type="button" onclick="apply()">Auswahl übernehmen</button>`;
}
function apply() {
  const f = document.getElementById("f");
  state.bore = f.bore.value; state.stroke = f.stroke.value; state.mount = f.mount.value;
  state.level = Math.min(3, state.level + 1);
  state.extra = location.hash === "#insert";
  render();                 // the whole form again: earlier element handles are gone
}
render();
</script></body></html>"""


def _cascade_site(p, fragment=""):
    async def make():
        browser = await p.chromium.launch()
        context = await browser.new_context()

        async def handle(route):
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=CASCADE)

        await context.route(f"{BASE}/**", handle)
        site = Site("form", BASE + "/form" + fragment, {"username": "u", "password": "p"}, context)
        await asyncio.wait_for(site.start(), timeout=40)
        return browser, site
    return make()


def _select_ref(observation: str, name: str) -> str:
    return re.search(r'\[(\w+)\] select "' + name, observation).group(1)


def test_a_chain_of_dependent_selects_takes_one_step():
    async def scenario():
        async with async_playwright() as p:
            browser, site = await _cascade_site(p)
            seen = await site.observe()
            apply = _ref(seen, "Auswahl übernehmen")
            result = await site.act("fill_fields", {"fields": [
                {"ref": _select_ref(seen, "Bohrung"), "value": "50", "apply": apply},
                {"ref": _select_ref(seen, "Hub"), "value": "600", "apply": apply},
                {"ref": _select_ref(seen, "Befestigung"), "value": "Flansch"}]})
            values = await site.page.evaluate("() => { const f = document.getElementById('f'); return [f.bore.value, f.stroke.value, f.mount.value]; }")
            await browser.close()
            return result, values
    result, values = asyncio.run(scenario())
    assert values == ["50", "600", "Flansch"] and "STOPPED" not in result and "ERROR" not in result


def test_a_chain_stops_when_the_form_changes_under_it():
    async def scenario():
        async with async_playwright() as p:
            browser, site = await _cascade_site(p, "#insert")
            seen = await site.observe()
            apply = _ref(seen, "Auswahl übernehmen")
            result = await site.act("fill_fields", {"fields": [
                {"ref": _select_ref(seen, "Bohrung"), "value": "50", "apply": apply},
                {"ref": _select_ref(seen, "Hub"), "value": "600", "apply": apply}]})
            stroke = await site.page.evaluate("() => document.getElementById('f').stroke.value")
            await browser.close()
            return result, stroke
    result, stroke = asyncio.run(scenario())
    assert "STOPPED, the form changed" in result and stroke == "" and "Observe the page and continue" in result


def test_a_labelled_scan_is_still_a_picture_to_read(monkeypatch):
    """Fake portal scanned/future-withdrawal: the canvas said only 'Scanned document attachment', got no ref, and
    the screenshots showed the tab button next to it."""
    async def fake_ask_json(system, user, **kwargs):
        return {"answer": "Staffel ab 10: 9,50", "uncertain": None}

    monkeypatch.setattr(llm, "ask_json", fake_ask_json)
    page = ("<html><body><button>3. Conditions and quantity tiers</button>"
            "<canvas aria-label='Scanned document attachment' width='500' height='300'></canvas></body></html>")

    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context()

            async def handle(route):
                await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=page)

            await context.route(f"{BASE}/**", handle)
            site = Site("scan2", BASE + "/cond", {"username": "u", "password": "p"}, context)
            await asyncio.wait_for(site.start(), timeout=40)
            observation = await site.observe()
            explorer = Explorer(site, Mission("t", "goal", {}, deadline=10**10), None)
            seen = await explorer._inspect_visual("Welche Staffeln?", _ref(observation, "3. Conditions"))
            await browser.close()
            return observation, seen
    observation, seen = asyncio.run(scenario())
    assert "[canvas: Scanned document attachment]" in observation
    assert re.search(r"\[\w+\] canvas \(500x300 px\)", observation) and "inspect_visual" in _hints(observation, True)
    assert "largest picture" in seen and "9,50" in seen
