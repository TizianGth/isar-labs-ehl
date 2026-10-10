"""Clef fallback for overlays with unknown wording. A fake Clef keeps these deterministic; one live test runs when
CLEF_BASE_URL is set (atira models proxy locally, or a run). Values are made up."""
import asyncio
import json
import os

import httpx
import pytest

from test_tools import run
from rfq.tools import decide, guards

NOTICE = """<dialog id=d><p>Neue Funktionen im Portal entdecken.</p>
<a href="/tour">Tour ansehen</a>
<button onclick="window.clicked='weiter'">Weiter zur Tour</button>
<button onclick="window.clicked='passt'; this.closest('dialog').close()">Passt so</button></dialog>
<p>Inhalt</p><script>document.getElementById('d').showModal()</script>"""


@pytest.fixture
def fake_clef(monkeypatch):
    calls = []

    async def choose(state, instructions, options, env=None):
        calls.append(dict(options))
        return next((k for k, v in options.items() if v == "Passt so"), None)
    monkeypatch.setattr(decide, "choose", choose)
    return calls


def _close(html, times=1):
    async def body(t, s):
        notes = []
        for _ in range(times):
            await s.page.set_content(html)
            notes += await guards.close_overlays(s)
        return notes, await s.page.evaluate("() => [document.getElementById('d').open, window.clicked]")
    return run(body)


def test_unknown_close_wording_is_decided_by_clef(fake_clef):
    notes, (still_open, clicked) = _close(NOTICE)
    assert still_open is False and clicked == "passt"
    assert "Passt so, Clef" in notes[0]
    # never offered: a button that continues somewhere, a link that navigates away
    assert list(fake_clef[0].values()) == ["Passt so"]


def test_recurring_overlay_costs_one_decision(fake_clef):
    _close(NOTICE, times=3)
    assert len(fake_clef) == 1


def test_no_safe_button_leaves_overlay_to_the_model(monkeypatch):
    async def none(*a, **k):
        return None
    monkeypatch.setattr(decide, "choose", none)
    notes, (still_open, clicked) = _close(NOTICE)
    assert still_open is True and clicked is None and notes == []


def test_known_wording_needs_no_decision(fake_clef):
    _close(NOTICE.replace("Passt so", "Schließen"))
    assert fake_clef == []


def _clef_response(choice, confidence):
    return httpx.Response(200, json={"answers": {"q": {"type": "choice", "choice": choice, "confidence": confidence}}})


@pytest.mark.parametrize("reply,expected", [
    (_clef_response("b1", 0.95), "b1"),
    (_clef_response("b1", 0.4), None),        # unsure
    (_clef_response("none", 0.99), None),     # nothing safe
    (_clef_response("b7", 0.99), None),       # not one of ours
    (httpx.Response(503), None),
])
def test_choose_interprets_answers(monkeypatch, reply, expected):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return reply
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(decide, "_failures", 0)
    env = {"CLEF_BASE_URL": "http://clef.test", "CLEF_API_KEY": "k"}
    assert asyncio.run(decide.choose({"dialog": "x"}, "q", {"b0": "A", "b1": "B"}, env=env)) == expected
    assert sent[0]["questions"]["q"]["criteria"] == {"b0": "A", "b1": "B", "none": "None of these"}


def test_choose_is_off_without_clef_or_when_switched_off():
    assert not decide.enabled({})
    assert not decide.enabled({"CLEF_BASE_URL": "http://clef.test", "RFQ_CLEF": "0"})


def test_choose_stops_after_repeated_failures(monkeypatch):
    monkeypatch.setattr(decide, "_failures", decide.MAX_FAILURES)
    assert not decide.enabled({"CLEF_BASE_URL": "http://clef.test"})


@pytest.mark.skipif(not os.environ.get("CLEF_BASE_URL"), reason="needs Clef (atira models proxy or a run)")
def test_live_clef_picks_the_dismiss_button(monkeypatch):
    monkeypatch.setattr(decide, "TIMEOUT_S", 300)  # a cold model takes minutes to wake
    notes, (still_open, clicked) = _close(NOTICE)
    assert still_open is False and clicked == "passt", notes
