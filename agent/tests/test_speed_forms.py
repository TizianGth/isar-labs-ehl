"""Synthetic browser regressions: partial batches never advance, plans bind fresh forms, resets are exclusive."""
import asyncio
from types import SimpleNamespace
import pytest

from playwright.async_api import async_playwright

from rfq.agent.form_plans import FormPlans
from rfq.agent.submit import SubmitExplorer
from rfq.browser.site import Site, _Session


@pytest.mark.parametrize("name,args", [
    ("click", {"ref": "final"}),
    ("fill_fields", {"fields": [{"ref": "price", "value": "99"}], "then_click": "final"}),
    ("fill_fields", {"fields": [{"ref": "price", "value": "99", "apply": "final"}]}),
])
def test_generic_tools_cannot_submit_without_review(name, args):
    class Button:
        async def evaluate(self, script):
            return {"text": "Angebot verbindlich abgeben", "id": "submit_bid", "name": ""}
    class FakeSite:
        async def _element(self, ref):
            return None, Button()
        async def act(self, name, args):
            raise AssertionError("unreviewed final action reached browser")
    explorer = SubmitExplorer(FakeSite(), SimpleNamespace(), None)
    result = asyncio.run(explorer._call(name, args))
    assert result.startswith("ERROR") and "submit_bid" in result


HTML = """<!doctype html><html lang=en><body><form id=offer>
<label>Position <input name=position readonly value=P1></label>
<label>Quantity <input name=quantity></label><label>Price <input name=price></label>
<label>Material <select name=material><option value=A>A</option><option value=B>B</option></select></label>
<button type=button id=save onclick='window.saved=(window.saved||0)+1'>Save and next</button>
</form></body></html>"""


async def _refs(site):
    await site.observe()
    return await site.page.evaluate("() => Object.fromEntries([...document.querySelectorAll('[name],#save')].map(e => [e.name || e.id,e.getAttribute('data-agent-ref')]))")


def test_real_form_batches_and_semantic_plans():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context()
            await context.route("https://fixture.test/**", lambda route: route.fulfill(status=200, content_type="text/html", body=HTML))
            site = Site("synthetic", "https://fixture.test", {}, context)
            await site.start()
            refs = await _refs(site)
            await site.page.locator('[name=quantity]').evaluate("e => e.disabled=true")
            result = await site.act("fill_fields", {"fields": [
                {"ref": refs["quantity"], "value": "2"}, {"ref": refs["price"], "value": "12"}], "then_click": refs["save"]})
            assert result.startswith("ERROR")
            assert await site.page.locator('[name=price]').input_value() == ""
            assert not await site.page.evaluate("window.saved")
            await site.page.locator('[name=quantity]').evaluate("e => e.disabled=false")
            # Scripts can undo a filled value; readback catches this before advancing.
            await site.page.locator('[name=quantity]').evaluate("e => e.oninput=() => {e.value='changed'}")
            result = await site.act("fill_fields", {"fields": [{"ref": refs["quantity"], "value": "2"}], "then_click": refs["save"]})
            assert result.startswith("ERROR") and not await site.page.evaluate("window.saved")
            await site.page.locator('[name=quantity]').evaluate("e => e.oninput=null")

            plans = FormPlans()
            refs = await _refs(site)
            try:
                await plans.remember(site, "unsafe", [{"ref": refs["quantity"], "value_key": "quantity"}],
                                     "P1", refs["position"], refs["save"])
            except ValueError:
                pass
            else:
                raise AssertionError("a plan must never execute a transition")
            await plans.remember(site, "lines", [{"ref": refs["quantity"], "value_key": "quantity"},
                {"ref": refs["price"], "value_key": "unit_price"}], "P1", refs["position"])
            answer = {"positions": [{"position": "P2", "quantity": 3, "unit_price": 14.5}]}
            # Fresh refs differ, but values must be taken from our computed answer.
            await site.page.evaluate("() => {document.querySelector('[name=position]').value='P2'; const a=document.createElement('a');a.href='#';a.textContent='Help';document.body.prepend(a)}")
            fresh = await _refs(site)
            assert fresh["quantity"] != refs["quantity"]
            result = await plans.apply(site, "lines", answer, "P2", fresh["position"])
            assert not result.startswith("ERROR")
            assert await site.page.locator('[name=quantity]').input_value() == "3"
            assert await site.page.locator('[name=price]').input_value() == "14.5"
            assert not await site.page.evaluate("window.saved")

            async def rejected():
                try:
                    await plans.apply(site, "lines", answer, "P2", fresh["position"])
                except ValueError:
                    return
                raise AssertionError("changed/uncertain forms must reject before writing")

            await site.page.locator('[name=material]').evaluate("e => e.add(new Option('C','C'))")
            await rejected()
            assert not await site.page.evaluate("window.saved")
            await site.page.locator('[name=material]').evaluate("e => e.options[2].remove()")
            await site.page.locator('[name=price]').evaluate("e => e.name='other-price'")
            await rejected()
            await site.page.locator('[name=other-price]').evaluate("e => e.name='price'")
            site.session.epoch += 1
            await rejected()
            assert (await site.act("type", {"ref": fresh["price"], "text": "999"})).startswith("ERROR")
            await site.observe()
            assert site._session_epoch == site.session.epoch
            await browser.close()
    asyncio.run(scenario())


def test_batch_apply_and_transition_errors_stop(monkeypatch):
    class Element:
        async def evaluate(self, script):
            if "tagName, e.type" in script:
                return ["INPUT", "text"]
            return "same"
    async def scenario():
        site = Site.__new__(Site)
        written, clicked = [], []
        async def element(ref): return None, Element()
        async def type_(ref, text): written.append(ref); return "typed"
        async def click(ref): clicked.append(ref); return "ERROR: disabled"
        site._element, site._do_type, site._do_click = element, type_, click
        result = await site._do_fill_fields([{"ref": "a", "value": "1", "apply": "save"}, {"ref": "b", "value": "2"}], "next")
        assert result.startswith("ERROR") and written == ["a"] and clicked == ["save"]
        written.clear(); clicked.clear()
        result = await site._do_fill_fields([{"ref": "a", "value": "1"}], "next")
        assert result.startswith("ERROR") and clicked == ["next"]
    asyncio.run(scenario())


def test_cookie_reset_waits_for_actions_and_blocks_new_actions():
    async def scenario():
        session = _Session()
        entered, release, resetting, release_reset = (asyncio.Event() for _ in range(4))
        order = []
        async def existing():
            async with session.operation():
                entered.set(); await release.wait(); order.append("old-done")
        async def reset():
            async with session.reset():
                order.append("reset"); resetting.set(); await release_reset.wait()
        async def new():
            async with session.operation(): order.append("new")
        a = asyncio.create_task(existing()); await entered.wait()
        b = asyncio.create_task(reset()); await asyncio.sleep(0)
        c = asyncio.create_task(new()); await asyncio.sleep(0)
        assert order == []
        release.set(); await resetting.wait(); assert order == ["old-done", "reset"]
        release_reset.set(); await asyncio.gather(a,b,c)
        assert order == ["old-done", "reset", "new"] and not session.readers
        # A reset reached inside wait_for inherits its parent's operation ownership.
        async with session.operation():
            async def nested():
                async with session.operation():
                    async with session.reset():
                        async with session.operation(): pass
            await asyncio.wait_for(nested(), timeout=1)
        assert not session.readers and not session.waiting and session.writer is None
    asyncio.run(scenario())


def test_confirmed_outcome_is_observed_before_any_wait():
    async def scenario():
        ex = SubmitExplorer.__new__(SubmitExplorer)
        async def observe(limit): return "Receipt accepted"
        async def act(*args): raise AssertionError("an already visible receipt must not wait")
        async def read(*args): return {"confirmed": True, "receipt": "synthetic-receipt"}
        ex.site = SimpleNamespace(observe=observe, act=act)
        ex._read = read
        assert (await ex._outcome())["confirmed"]
    asyncio.run(scenario())


def test_session_cancellation_releases_readers_and_reset_waiters():
    async def scenario():
        session = _Session()
        entered, release = asyncio.Event(), asyncio.Event()
        async def reader():
            async with session.operation():
                entered.set(); await release.wait()
        async def reset():
            async with session.operation():
                async with session.reset(): pass
        a = asyncio.create_task(reader()); await entered.wait()
        b = asyncio.create_task(reset()); await asyncio.sleep(0); await asyncio.sleep(0)
        b.cancel(); await asyncio.gather(b, return_exceptions=True)
        assert session.waiting == 0 and session.writer is None
        a.cancel(); await asyncio.gather(a, return_exceptions=True)
        assert not session.readers
        async with session.reset():
            async def blocked():
                async with session.operation(): pass
            # A task from a different context must not inherit the writer's permission.
            import contextvars
            c = asyncio.create_task(blocked(), context=contextvars.Context())
            await asyncio.sleep(0); c.cancel(); await asyncio.gather(c, return_exceptions=True)
        assert not session.readers and not session.waiting
    asyncio.run(scenario())


def test_recovery_epoch_shared_between_tabs_and_limits_preserved():
    async def scenario():
        async def clear(): pass
        context = SimpleNamespace(clear_cookies=clear)
        site = Site("x", "https://fixture.test", {}, context)
        other = Site("x", "https://fixture.test", {}, context)
        assert site.session is other.session
        site.health["fails"] = 2
        events = []
        async def goto(*args, **kwargs): events.append("root")
        async def fresh():
            assert site.session.writer is not None
            site.page = SimpleNamespace(goto=goto)
        async def settle(**kwargs):
            assert site.session.writer is not None
            events.append("login")
        site._fresh_page, site.settle = fresh, settle
        async with site.session.operation():
            assert await asyncio.wait_for(site._new_session("https://fixture.test/selected"), 1)
        assert events == ["root", "login"] and site.session.epoch == 1
        assert site._session_epoch == 1 and other._session_epoch == 0
        assert (await other.act("click", {"ref": "e1"})).startswith("ERROR")
        site.health["fails"] = 2
        assert not await site._new_session("https://fixture.test/selected")  # once/minute
        assert site.session.epoch == 1 and not site.session.readers
    asyncio.run(scenario())


def test_settle_deduplication_only_for_navigation_actions():
    async def scenario():
        site = Site("x", "https://fixture.test", {}, None)
        calls = []
        async def ensure(): pass
        async def responsive(): return True
        async def guards(*args): pass
        async def settle(): calls.append("settle"); site._settle_version += 1
        async def method(): await settle(); return "ok"
        site._ensure_page, site.responsive, site._run_guards_everywhere = ensure, responsive, guards
        site.settle = settle
        site._do_click = method
        await site.act("click", {})
        assert calls == ["settle"]
        calls.clear(); site._do_fill_fields = method
        await site.act("fill_fields", {})
        assert calls == ["settle", "settle"]  # fields can change after their dependent selection's settle
    asyncio.run(scenario())
