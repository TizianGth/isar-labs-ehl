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
    site.fresh_start()
    assert site.hung == {} and site._recoveries == 0


def _cascade_page(query: dict) -> str:
    a, b = query.get("a", ""), query.get("b", "")
    notice = '<a href="/help">Hinweis zur Auswahl</a>' if a else ""      # shifts every ref after the first apply
    opts = lambda name, values, chosen: "".join(f'<option{" selected" if v == chosen else ""}>{v}</option>' for v in values)
    return f"""<html><body>{notice}<form action="/form" method="get">
      <label>Größe <select name="a">{opts("a", ["-", "S", "M"], a)}</select></label>
      <button type="submit">Übernehmen</button>
      <label>Farbe <select name="b" {"" if a else "disabled"}>{opts("b", ["-", "rot", "blau"], b)}</select></label>
      <label>Anzahl <input name="n" value=""></label>
      <button type="submit" formaction="/done">Weiter</button></form></body></html>"""


def test_steps_do_a_whole_form_in_one_action_across_rerenders():
    """N1: one `steps` call does filter, apply, dependent filter, apply, fields and 'next', although every apply
    re-renders the page and renumbers its refs."""
    from urllib.parse import parse_qs, urlparse

    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context()

            async def handle(route):
                url = urlparse(route.request.url)
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                if url.path == "/done":
                    body = f"<html><body><p>Fertig a={query.get('a')} b={query.get('b')} n={query.get('n')}</p></body></html>"
                else:
                    body = _cascade_page(query)
                await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

            await context.route(f"{BASE}/**", handle)
            site = Site("form", BASE, {"username": "u", "password": "p"}, context)
            await site.start()
            await site.goto(f"{BASE}/form")
            seen = await site.observe()
            ref = lambda kind, name: re.search(r'\[(\w+)\] ' + re.escape(kind) + r' "' + re.escape(name), seen).group(1)
            size, apply, colour, count, nxt = (ref("select", "Größe"), ref("button", "Übernehmen"), ref("select", "Farbe"),
                                               ref("input[text]", "Anzahl"), ref("button", "Weiter"))
            result = await site.act("steps", {"steps": [
                {"action": "select", "ref": size, "option": "S", "then_click": apply},
                {"action": "select", "ref": colour, "option": "blau", "then_click": apply},
                {"action": "fill_fields", "fields": [{"ref": count, "value": "7"}]},
                {"action": "click", "ref": nxt},
                {"action": "type", "ref": count, "text": "8"},
                {"action": "click", "ref": nxt}]})
            final = await site.observe()
            await browser.close()
            return result, final
    result, final = asyncio.run(scenario())
    assert "Fertig a=S b=blau n=7" in final
    assert "5. type: ERROR" in result and "no longer on the page" in result and "STOPPED: 1 later action" in result


def test_rematch_follows_controls_by_kind_name_and_order():
    from rfq.browser.site import _rematch
    before = '[e1] button "OK"\n[e2] select "Größe" options: a\n[e3] button "OK"'
    after = '[e1] link "Hinweis" -> /h\n[e2] button "OK"\n[e3] select "Größe" options: a\n[e4] button "OK"'
    assert _rematch({"action": "select", "ref": "e2", "then_click": "e3"}, before, after) == \
        {"action": "select", "ref": "e3", "then_click": "e4"}
    assert _rematch({"action": "fill_fields", "fields": [{"ref": "[e1]", "value": "x"}]}, before, after)["fields"] == \
        [{"ref": "e2", "value": "x"}]
    assert "no longer on the page" in _rematch({"action": "click", "ref": "e2"}, before, '[e1] button "OK"')
