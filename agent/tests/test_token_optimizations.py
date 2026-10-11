"""Tests for token usage and latency optimizations:
- Prompt cache activation (EXTRACT_SYSTEM token length >= 1,024)
- Compact history ring buffer in Explorer loop
- Early completion of mail_progress when wanted topics are fulfilled
- Early completion of erp_progress when active matching materials have scales
- Batched URL extraction via extract_many
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from rfq.agent.explorer import Explorer, Mission
from rfq.agent.orchestrator import Blackboard, Orchestrator
from rfq.agent.prompts import EXTRACT_SYSTEM


def test_extract_system_token_length():
    """EXTRACT_SYSTEM must be sufficiently detailed (>= 4,000 chars / ~1,024 tokens) to trigger platform prompt caching."""
    assert len(EXTRACT_SYSTEM) >= 4000, f"EXTRACT_SYSTEM is {len(EXTRACT_SYSTEM)} chars, expected >= 4000"


def test_mail_progress_early_completion():
    """mail_progress must complete immediately once all required topics are present, ignoring decoys."""
    orch = Orchestrator({"brief": "Tender VG-1", "expires_at": 10**12}, {})
    orch.brief = {
        "tender_reference": "VG-1",
        "scenario_datetime_utc": "2026-10-10T12:00:00Z",
        "director_localpart": "leitung",
        "authorized_records": [
            {"topic": "packaging", "subject_keyword": "Verpackung", "sender_localpart": "logistik"},
        ],
    }

    # Add mailbox listing with 5 messages (1 discount, 1 packaging, 3 decoys)
    orch.bb.add("mail_list", {
        "mailbox_address": "sales@bidder.com",
        "folder": "inbox",
        "page": 1,
        "pages_total": 1,
        "messages": [
            {"subject": "VG-1 Rabatt", "url": "/msg/1"},
            {"subject": "VG-1 Verpackung", "url": "/msg/2"},
            {"subject": "VG-1 Decoy 1", "url": "/msg/3"},
            {"subject": "VG-1 Decoy 2", "url": "/msg/4"},
            {"subject": "VG-1 Decoy 3", "url": "/msg/5"},
        ],
    }, "/list")

    # Add extractions for only the 2 wanted topics
    orch.bb.add("mail_message", {
        "subject": "VG-1 Rabatt",
        "sender": "leitung@bidder.com",
        "datetime_utc": "2026-10-09T10:00:00Z",
        "reference": "VG-1",
        "is_draft_or_preview": False,
        "topic": "discount",
        "data": {"is_approval": True, "percent": "5"},
    }, "/msg/1")

    orch.bb.add("mail_message", {
        "subject": "VG-1 Verpackung",
        "sender": "logistik@bidder.com",
        "datetime_utc": "2026-10-09T11:00:00Z",
        "reference": "VG-1",
        "is_draft_or_preview": False,
        "topic": "packaging",
        "data": {"tariffs": {"T1": {"eur_per_carton": "12.50"}}},
    }, "/msg/2")

    text, complete = orch.mail_progress()
    # Even though /msg/3, /msg/4, /msg/5 are unopened, complete must be True!
    assert complete is True, f"Expected mail_progress to complete, got False. Text: {text}"
    assert "topics NOT found yet" not in text


def test_erp_progress_early_completion():
    """erp_progress must complete once all requirements are satisfied by an active material with scales."""
    orch = Orchestrator({"brief": "Tender VG-1", "expires_at": 10**12}, {})
    reqs = [{
        "ref": "A-1",
        "bore_mm": "50",
        "stroke_mm": "600",
        "pressure_bar": "210",
        "mounting": "Schwenkauge",
        "certification": None,
        "catalog_group": "GR-1",
    }]

    # Material 1: active, matches A-1, has price conditions and scales
    orch.bb.add("materials", {
        "materials": [{
            "material_number": "HZ-1",
            "catalog_group": "GR-1",
            "bore_mm": "50",
            "stroke_mm": "600",
            "pressure_bar": "210",
            "mounting": "Schwenkauge",
            "certification": None,
            "status_meaning": "active",
            "unrestricted_stock": "20",
            "lead_time_stock_weeks": "2",
            "lead_time_production_weeks": "4",
            "pieces_per_carton": "10",
            "conditions": [{
                "valid_from": "2026-01-01",
                "currency": "EUR",
                "scales": [{"from_qty": "1", "price": "100.00"}],
            }],
        }, {
            # Material 2: blocked or alternate in same group
            "material_number": "HZ-2",
            "catalog_group": "GR-1",
            "bore_mm": "50",
            "stroke_mm": "600",
            "pressure_bar": "210",
            "mounting": "Schwenkauge",
            "status_meaning": "blocked",
            "conditions": [],
        }],
    }, "/mm03")

    text, complete = orch.erp_progress("GR-1", reqs)
    assert complete is True, f"Expected erp_progress to complete, got False. Text: {text}"


def test_extract_many_batches_items(monkeypatch):
    """extract_many batches up to 3 items into one LLM call and records Blackboard items."""
    from rfq import llm as llm_module

    call_count = 0

    async def fake_ask_json(system, prompt, **kwargs):
        nonlocal call_count
        call_count += 1
        return {
            "results": [
                {"url": "http://site/item1", "data": {"material_number": "HZ-1"}},
                {"url": "http://site/item2", "data": {"material_number": "HZ-2"}},
            ]
        }

    monkeypatch.setattr(llm_module, "ask_json", fake_ask_json)
    orch = Orchestrator({"brief": "Tender VG-1", "expires_at": 10**12}, {})
    orch.brief = {"tender_reference": "VG-1", "scenario_datetime_utc": "2026-10-10T12:00:00Z"}

    items = [
        ("http://site/item1", "Text of item 1"),
        ("http://site/item2", "Text of item 2"),
    ]

    summaries = asyncio.run(orch.extract_many("materials", items))
    assert len(summaries) == 2
    assert call_count == 1, f"Expected 1 batch call, got {call_count}"
    assert len(orch.bb.get("materials")) == 2
