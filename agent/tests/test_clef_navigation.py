"""Offline protocol/abstention tests, NOT Clef's semantic accuracy evaluation."""
import asyncio
from dataclasses import replace
import json
import time

import httpx
import pytest

from rfq import clef


PAGE = '''URL: https://synthetic.invalid/workbench
[e2] link "Archive" -> /records/old
Current requirement documents
[f1e8] link "Requirement document" -> /reader/new-layout?id=opaque
[e9] link "Support" -> /help
[e10] link "Logout" -> /logout
'''


def answer(payload, chosen=None):
    criteria = payload["questions"]["next"]["criteria"]
    if chosen is None:
        chosen = next(key for key, label in criteria.items()
                      if '"Requirement document"' in label.split("; frame=", 1)[0])
    probabilities = {key: 0.03 / (len(criteria) - 1) for key in criteria}
    probabilities[chosen] = 0.97
    return {"answers": {"next": {"type": "choice", "choice": chosen,
                                "confidence": 0.97, "probabilities": probabilities}},
            "usage": {"input_tokens": 100, "output_tokens": 0}}


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("RFQ_CLEF_NAV", "1")
    monkeypatch.setenv("CLEF_BASE_URL", "https://proxy.synthetic.invalid/clef")
    monkeypatch.setenv("CLEF_API_KEY", "synthetic-only")
    monkeypatch.setattr(clef, "_slots", asyncio.Semaphore(4))
    events = []
    monkeypatch.setattr(clef, "log", lambda event, **kwargs: events.append((event, kwargs)))
    return events


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    settings = []

    def create(**kwargs):
        settings.append(kwargs)
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(clef.httpx, "AsyncClient", create)
    return settings


@pytest.mark.parametrize("reverse,new_url", [(False, False), (True, False), (False, True), (True, True)])
def test_candidate_permutation_and_unseen_url_are_model_choices(monkeypatch, enabled, reverse, new_url):
    page = PAGE.replace("/reader/new-layout?id=opaque", "/changed-ui/unknown?q=arbitrary") if new_url else PAGE
    candidates = clef.observed_candidates(page)
    if reverse:
        candidates.reverse()
    requests = []

    def handler(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert set(payload) == {"state", "questions"}  # Atira selects hosted model
        assert payload["state"]["page"] == page
        return httpx.Response(200, json=answer(payload))

    settings = transport(monkeypatch, handler)
    result = asyncio.run(clef.choose_navigation("Read the requirement document", page, candidates,
                                              observation_id=clef.snapshot_id(page)))
    assert result == next(c for c in candidates if c.label == "Requirement document")
    assert result.frame == "f1" and all(c.label != "Logout" for c in candidates)
    assert str(requests[0].url) == "https://proxy.synthetic.invalid/clef/v1/decide"
    assert requests[0].headers["Authorization"] == "Bearer synthetic-only"
    assert settings[0]["trust_env"] is False and settings[0]["follow_redirects"] is False
    assert enabled[-1][1]["status"] == "selected"
    assert "synthetic-only" not in str(enabled)


def test_default_off_performs_no_request(monkeypatch):
    monkeypatch.delenv("RFQ_CLEF_NAV", raising=False)

    async def forbidden(*args, **kwargs):
        pytest.fail("default-off path requested a model")

    monkeypatch.setattr(clef, "decide", forbidden)
    assert asyncio.run(clef.choose_navigation("Read", PAGE, clef.observed_candidates(PAGE))) is None


@pytest.mark.parametrize("changed", [
    lambda cs: [replace(cs[0], href="/invented-destination")],
    lambda cs: [replace(cs[0], label="Invented content")],
    lambda cs: [replace(cs[0], ref="e444")],
    lambda cs: [replace(cs[0], frame="f9")],
    lambda cs: [replace(cs[0], observation_id="old-snapshot")],
    lambda cs: [replace(cs[0], context="unobserved answer")],
    lambda cs: [cs[0], cs[0]],
    lambda cs: [],
    lambda cs: ["invalid candidate"],
])
def test_invalid_candidates_never_reach_model(monkeypatch, enabled, changed):
    async def forbidden(*args, **kwargs):
        pytest.fail("unobserved/stale candidate reached the model")

    monkeypatch.setattr(clef, "decide", forbidden)
    assert asyncio.run(clef.choose_navigation("Read", PAGE, changed(clef.observed_candidates(PAGE)))) is None


def test_changed_same_url_page_invalidates_refs(monkeypatch, enabled):
    async def forbidden(*args, **kwargs):
        pytest.fail("stale refs reached the model")

    monkeypatch.setattr(clef, "decide", forbidden)
    candidates = clef.observed_candidates(PAGE)
    assert asyncio.run(clef.choose_navigation("Read", PAGE + "New revision appeared", candidates)) is None
    assert asyncio.run(clef.choose_navigation("Read", PAGE, candidates, observation_id="previous-state")) is None


@pytest.mark.parametrize("label,href", [
    ("Submit", "/action"), ("Sign", "/action"), ("Delete", "/action"),
    ("Read", "javascript:submit()"), ("Read", "data:text/html,read"),
    ("Read", "https://user:password@synthetic.invalid/a"), ("Read", "/confirm"),
])
def test_form_mutation_or_nonweb_targets_abstain(monkeypatch, enabled, label, href):
    async def forbidden(*args, **kwargs):
        pytest.fail("unsafe link reached the model")

    monkeypatch.setattr(clef, "decide", forbidden)
    page = f'[e1] link "{label}" -> {href}'
    candidates = [clef.Candidate("e1", label, href)]
    assert asyncio.run(clef.choose_navigation("Read", page, candidates)) is None


@pytest.mark.parametrize("malform", [
    lambda a: {},
    lambda a: {"answers": {"next": {"type": "choice", "choice": "c999"}}},
    lambda a: {"answers": {"next": {"type": "choice", "choice": "c1", "confidence": 1}}},
    lambda a: a | {"answers": {"next": a["answers"]["next"] | {"choice": "c0"}}},
    lambda a: a | {"answers": {"next": a["answers"]["next"] | {"confidence": 1.5}}},
    lambda a: a | {"answers": {"next": a["answers"]["next"] | {"type": "score"}}},
    lambda a: a | {"answers": {"next": a["answers"]["next"] | {"probabilities": {"c1": float("nan")}}}},
])
def test_malformed_or_inconsistent_responses_fall_back(monkeypatch, enabled, malform):
    async def decide(payload, **kwargs):
        return malform(answer(payload))

    monkeypatch.setattr(clef, "decide", decide)
    assert asyncio.run(clef.choose_navigation("Read", PAGE, clef.observed_candidates(PAGE))) is None


@pytest.mark.parametrize("probabilities,choice", [
    ({"c0": 0.2, "c1": 0.6, "c2": 0.1, "abstain": 0.1}, "c1"),
    ({"c0": 0.01, "c1": 0.01, "c2": 0.01, "abstain": 0.97}, "abstain"),
    ({"c0": 0.1, "c1": 0.97, "c2": 0.1, "abstain": 0.1}, "c1"),
])
def test_uncertainty_abstention_and_invalid_normalization(monkeypatch, enabled, probabilities, choice):
    async def decide(payload, **kwargs):
        return {"answers": {"next": {"type": "choice", "choice": choice, "probabilities": probabilities}}}

    monkeypatch.setattr(clef, "decide", decide)
    assert asyncio.run(clef.choose_navigation("Read", PAGE, clef.observed_candidates(PAGE))) is None


def test_timeout_and_expired_deadline_fall_back(monkeypatch, enabled):
    called = []
    cancelled = []

    async def decide(payload, **kwargs):
        called.append(True)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(clef, "decide", decide)
    monkeypatch.setattr(clef, "REQUEST_TIMEOUT", 0.12)
    result = asyncio.run(clef.choose_navigation("Read", PAGE, clef.observed_candidates(PAGE)))
    assert result is None and called == cancelled == [True]
    assert enabled[-1][1]["status"] == "fallback:TimeoutError"
    assert asyncio.run(clef.choose_navigation("Read", PAGE, clef.observed_candidates(PAGE),
                                            deadline=time.time() - 1)) is None
    assert called == [True]


def test_external_cancellation_is_not_swallowed_and_frees_slot(monkeypatch, enabled):
    started = asyncio.Event()

    async def decide(payload, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(clef, "decide", decide)

    async def scenario():
        task = asyncio.create_task(clef.choose_navigation("Read", PAGE, clef.observed_candidates(PAGE)))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        for _ in range(4):
            await asyncio.wait_for(clef._slots.acquire(), 0.05)
        assert clef._slots.locked()

    asyncio.run(scenario())
    assert enabled[-1][1]["status"] == "cancelled"


def test_redirect_and_http_error_do_not_leak_to_second_provider(monkeypatch, enabled):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://outside.synthetic.invalid"})

    transport(monkeypatch, handler)
    assert asyncio.run(clef.choose_navigation("Read", PAGE, clef.observed_candidates(PAGE))) is None
    assert len(requests) == 1


def test_large_decision_set_is_not_silently_truncated(monkeypatch, enabled):
    page = "\n".join(f'[e{i}] link "Document {i}" -> /opaque/{i}' for i in range(30))

    async def forbidden(*args, **kwargs):
        pytest.fail("incomplete candidate set reached the model")

    monkeypatch.setattr(clef, "decide", forbidden)
    assert asyncio.run(clef.choose_navigation("Read", page, clef.observed_candidates(page))) is None
