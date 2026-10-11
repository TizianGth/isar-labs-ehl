"""Synthetic navigation budgets and binding safety without a model or external network."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from playwright.async_api import Error as PlaywrightError, async_playwright

import rfq.browser.site as site_module
from rfq.browser.site import Site


def test_commit_and_functional_readiness_have_explicit_limits(monkeypatch):
    async def scenario():
        page = MagicMock(url="http://synthetic.test/old")
        page.goto = AsyncMock(return_value=MagicMock(status=200))
        page.evaluate = AsyncMock(return_value={"ready": True})
        site = Site("test", "http://synthetic.test", {}, None)
        site.page = page
        assert await site._navigate(page, "http://synthetic.test/new")
        assert page.goto.await_args.kwargs == {"wait_until": "commit", "timeout": 4000}
        assert page.evaluate.await_count == 1
        page.wait_for_load_state.assert_not_called()
    asyncio.run(scenario())


def test_timeout_never_accepts_the_previous_document():
    async def scenario():
        page = MagicMock(url="http://synthetic.test/old")
        page.goto = AsyncMock(side_effect=PlaywrightError("commit timeout"))
        page.evaluate = AsyncMock(return_value={"ready": True})
        site = Site("test", "http://synthetic.test", {}, None)
        site.page = page
        assert not await site._navigate(page, "http://synthetic.test/new")
        page.evaluate.assert_not_awaited()
    asyncio.run(scenario())


def test_only_one_read_only_recovery_attempt(monkeypatch):
    async def scenario():
        site = Site("test", "http://synthetic.test", {}, None)
        site.page = MagicMock()
        site._navigate = AsyncMock(return_value=False)
        site._fresh_page = AsyncMock()
        site._new_session = AsyncMock(return_value=False)
        assert not await site.goto("http://synthetic.test/new")
        assert site._navigate.await_count == 2
        assert site._fresh_page.await_count == 1
    asyncio.run(scenario())


def test_whole_attempt_budget_cancels_hanging_recovery(monkeypatch):
    monkeypatch.setattr(site_module, "NAVIGATION_BUDGET", 0.04)

    async def scenario():
        site = Site("test", "http://synthetic.test", {}, None)
        site.page = MagicMock()
        site._new_session = AsyncMock(return_value=False)
        cancelled = asyncio.Event()

        async def hangs(*args):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        site._goto_fast = hangs
        assert not await site.goto("http://synthetic.test/new")
        assert cancelled.is_set()
    asyncio.run(scenario())


def test_startup_is_shared_between_waiters():
    async def scenario():
        site = Site("test", "http://synthetic.test", {}, None)
        site._start = AsyncMock(return_value="attached")
        first = site.start_task()
        assert first is site.start_task()
        assert await asyncio.gather(site.wait_started(), site.wait_started()) == ["attached", "attached"]
        site._start.assert_awaited_once()
    asyncio.run(scenario())


def test_failed_write_click_is_not_replayed():
    async def scenario():
        site = Site("test", "http://synthetic.test", {}, None)
        site.page = MagicMock()
        el = MagicMock()
        el.evaluate = AsyncMock(return_value="BUTTON")
        el.click = AsyncMock(side_effect=PlaywrightError("navigation timed out after click"))
        site._element = AsyncMock(return_value=(MagicMock(), el))
        site._watch_popups = MagicMock(return_value=[])
        site._unwatch_popups = MagicMock()
        try:
            await site._do_click("e1")
        except PlaywrightError:
            pass
        else:
            raise AssertionError("ambiguous click should require observation")
        assert el.click.await_count == 1
        el.dispatch_event.assert_not_called()
    asyncio.run(scenario())


def test_unannounced_rerender_stops_remaining_fields():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context()
            await context.route("http://synthetic.test/**", lambda route: route.fulfill(
                content_type="text/html", body="""<html><body><form id="f">
                <label>First<input name="first" oninput="document.querySelector('[name=second]').outerHTML = '&lt;input name=second&gt;'"></label>
                <label>Second<input name="second"></label></form></body></html>"""))
            site = Site("test", "http://synthetic.test", {}, context)
            await site.start()
            await site.page.evaluate("""() => {document.querySelector('[name=first]').setAttribute('data-agent-ref','e1');
                document.querySelector('[name=second]').setAttribute('data-agent-ref','e2'); }""")
            result = await site._do_fill_fields([{"ref": "e1", "value": "alpha"}, {"ref": "e2", "value": "beta"}])
            assert "STOPPED" in result or "ERROR" in result
            assert await site.page.locator('[name=second]').input_value() == ""
            assert len(site.last_readbacks) == 1
            await browser.close()
    asyncio.run(scenario())


def test_later_field_reset_blocks_final_click():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context()
            await context.route("http://synthetic.test/**", lambda route: route.fulfill(
                content_type="text/html", body="""<html><body><form id="f">
                <label>First<input name="first"></label>
                <label>Second<input name="second" oninput="document.querySelector('[name=first]').value = ''"></label>
                <button type="button" onclick="window.sent = true">Continue</button></form></body></html>"""))
            site = Site("test", "http://synthetic.test", {}, context)
            await site.start()
            await site.page.evaluate("""() => {document.querySelector('[name=first]').setAttribute('data-agent-ref','e1');
                document.querySelector('[name=second]').setAttribute('data-agent-ref','e2');
                document.querySelector('button').setAttribute('data-agent-ref','e3'); }""")
            result = await site._do_fill_fields([{"ref": "e1", "value": "alpha"}, {"ref": "e2", "value": "beta"}], then_click="e3")
            assert "previously applied field changed" in result
            assert not await site.page.evaluate("!!window.sent")
            await browser.close()
    asyncio.run(scenario())


def test_error_guard_never_reloads_an_ambiguous_write():
    async def scenario():
        site = Site("test", "http://synthetic.test", {}, None)
        site.page = MagicMock(url="http://synthetic.test/outcome")
        site._ensure_page = AsyncMock(return_value=site.page)
        site._guard_state = AsyncMock(return_value={"error_page": True})
        site._safe_goto = AsyncMock()
        await site._settle(3, retry_errors=False)
        site._safe_goto.assert_not_awaited()
    asyncio.run(scenario())


_APPLY_FORM = """<html><body><div id="fields"></div><script>
function render(applied, choice) {
    document.getElementById('fields').innerHTML = `<form action="/save" method="post">
    <label>Variant<select name="variant"><option>alpha</option><option>omega</option></select></label>
    <button type="button" id="apply">Apply variant</button>
    <label>Quantity<input name="quantity" ${applied ? '' : 'disabled'}></label>
    <label>Price<input name="price" ${applied ? '' : 'disabled'}></label>
    <button type="submit">Save record</button></form>`;
    document.querySelector('[name=variant]').value = choice;
    document.getElementById('apply').onclick = () => render(true, document.querySelector('[name=variant]').value);
}
render(false, 'alpha');</script></body></html>"""


async def _apply_site(playwright, requests):
    browser = await playwright.chromium.launch()
    context = await browser.new_context()

    async def handle(route):
        requests.append(route.request.method)
        if route.request.method == 'POST':
            await route.fulfill(status=502, content_type='text/html',
                                body='<html><body><h1>502 Bad Gateway</h1><p>Service unavailable</p></body></html>')
        else:
            await route.fulfill(content_type='text/html', body=_APPLY_FORM)
    await context.route('https://synthetic.test/**', handle)
    site = Site('test', 'https://synthetic.test', {}, context)
    await site.start()
    return browser, site


def _label_ref(observation, label):
    import re
    return re.search(r'\[(e\d+)\][^\n]*' + re.escape(label), observation).group(1)


def test_multiple_fields_after_explicit_apply_remain_valid():
    async def scenario():
        async with async_playwright() as playwright:
            browser, site = await _apply_site(playwright, [])
            view = await site.observe()
            result = await site.act('fill_fields', {'fields': [
                {'ref': _label_ref(view, 'Variant'), 'value': 'omega', 'apply': _label_ref(view, 'Apply variant')},
                {'ref': _label_ref(view, 'Quantity'), 'value': '7'},
                {'ref': _label_ref(view, 'Price'), 'value': '13.20'}]})
            assert 'STOPPED' not in result and 'ERROR' not in result
            assert await site.page.locator('[name=quantity]').input_value() == '7'
            assert await site.page.locator('[name=price]').input_value() == '13.20'
            assert len(site.last_readbacks) == 3
            await browser.close()
    asyncio.run(scenario())


def test_error_page_after_write_reports_unknown_without_replaying():
    async def scenario():
        requests = []
        async with async_playwright() as playwright:
            browser, site = await _apply_site(playwright, requests)
            result = await site.act('click', {'ref': _label_ref(await site.observe(), 'Save record')})
            assert 'OUTCOME UNKNOWN' in result
            assert site.write_outcome_unknown
            assert requests.count('POST') == 1
            assert requests.count('GET') == 1
            await browser.close()
    asyncio.run(scenario())
