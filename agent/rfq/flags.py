"""Feature switches, read from the environment once at start (1/true/on or 0/false/off).

Defaults are the behaviour validated in sandbox test runs; a test run can switch a feature on or off with
dev/eval/make_variant.py, without changing agent/. Code reads them as `flags.NAME` at call time, so tests can
monkeypatch them. Status and validation of every switch: docs/13-optimization-roadmap.md.
"""
import os


def _on(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in ("1", "true", "yes", "on")


# shipped and validated (switches exist for a quick rollback)
BACKGROUND_EXTRACTION = _on("RFQ_BACKGROUND_EXTRACTION", True)  # ERP and mail missions navigate on while extracting
REVIEW_PREFETCH = _on("RFQ_REVIEW_PREFETCH", True)              # read the review page while the TAN arrives
STEPS_TOOL = _on("RFQ_STEPS_TOOL", True)                        # submission: several form actions in one step
START_OVERLAP = _on("RFQ_START_OVERLAP", True)                  # websites load while the brief is read

# robustness (on: no effect unless the situation occurs)
TAB_ISOLATION = _on("RFQ_TAB_ISOLATION", True)                  # own tab when two roles share one website
GAP_RECOVERY = _on("RFQ_GAP_RECOVERY", True)                    # look for missing facts on another website

# performance steps: off until paired test runs confirm them (docs/13 §4)
BRIEF_SPLIT = _on("RFQ_BRIEF_SPLIT", False)                     # core brief fields first, the rest in parallel
WAIT_ON_BACKGROUND = _on("RFQ_WAIT_ON_BACKGROUND", False)       # wait_for returns when a background result arrives
LLM_LANES = _on("RFQ_LLM_LANES", False)                         # separate model slots for navigation and extraction
EXTRACT_THEN = _on("RFQ_EXTRACT_THEN", False)                   # extract(..., then_click) / extract_urls(..., then_open)
LINKS_IN_RESULTS = _on("RFQ_LINKS_IN_RESULTS", False)           # extract_urls results list the links found there
COMPACT_HISTORY = _on("RFQ_COMPACT_HISTORY", False)             # older steps as one short line each
SCHEMA_TRIM = _on("RFQ_SCHEMA_TRIM", False)                     # extraction schemas without copied prose
PREPARE_BID = _on("RFQ_PREPARE_BID", False)                     # fill the bidder step of the bid form early
