"""Browser tools for the site agents.

observe.js / observe.py  page → compact text with refs (all frames; hidden decoys dropped; tooltips, closed
                         <details>, canvas/role=img labels, ::before labels, <time datetime>, logical order shown)
guards.py                automatic mechanics around every action: settle, overlays, cookie walls, relogin, error retry
actions.py               BrowserTools (model-facing tools) + TOOL_SPECS (OpenAI function schemas) + call_tool()
documents.py             CSV/TXT/HTML/ZIP/PDF behind a chosen link → text
decide.py                Clef decisions for guards that run out of rules (off without CLEF_BASE_URL or with RFQ_CLEF=0)
session.py               SiteSession per website: CDP/local connection, dialog/popup/response handlers
"""
from .actions import TOOL_SPECS, BrowserTools, call_tool
from .session import SiteSession, open_context

__all__ = ["TOOL_SPECS", "BrowserTools", "SiteSession", "call_tool", "open_context"]
