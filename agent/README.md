# isar-labs RFQ agent (package root)

This folder is the package we upload (`atira test-run ./agent`, `atira submit ./agent`). Only generic code lives
here — no values of any scenario. Design: `../docs/02-architecture.md`; rules: `../docs/04-business-rules.md`.

## Layout

| Path | Role |
| --- | --- |
| `main.py` | Entry point: reads the run, opens one browser (sandbox: CDP) per website, runs the orchestrator |
| `rfq/contract.py` | Run contract (API / local scouting data), clock |
| `rfq/llm.py` | Qwen client: retries, concurrency limit, thinking on/off, JSON mode, usage log |
| `rfq/browser/observe.js` | DOM → text serializer (drops decoys, keeps tooltips/details/canvas labels, refs) |
| `rfq/browser/guards.js` | Generic guards: login detection, cookie walls, overlays, unlock gates, error pages |
| `rfq/browser/site.py` | One website in one tab: guards around every action, popups, relogin, helper tabs |
| `rfq/agent/explorer.py` | Model-driven tool loop (one tool per step) with progress checklist and loop protection |
| `rfq/agent/orchestrator.py` | Brief → missions in parallel (tender, specification, mail, ERP per catalogue group) → rules → submit |
| `rfq/agent/prompts.py` | System prompts and extraction schemas (site-agnostic) |
| `rfq/agent/submit.py` | Model-driven bid form filling, TAN from the mailbox, final confirmation with summary check |
| `rfq/rules/parse.py` | Numbers with locale tags, dates, identifiers |
| `rfq/rules/resolve.py` | Deterministic rules + pricing (Decimal, HALF_UP) with a decision trace |
| `tests/` | Unit tests (synthetic data only) |

## Run locally

```bash
uv sync && uv run playwright install chromium     # once
atira models proxy                                   # terminal 1 (hosted Qwen on 127.0.0.1:8765)
eval "$(atira scout --env)"                          # terminal 2: scouting logins + model variables
uv run --no-sync python main.py --dry-run --out ../dev/runs/local.json > ../dev/runs/local.log
uv run --no-sync python ../dev/eval/score.py ../dev/runs/local.json   # field-by-field vs. dev oracle
uv run --no-sync pytest -q
```

`--dry-run` stops before the binding submission. `HEADLESS=0` shows the browser.
Env knobs: `RFQ_MODEL`, `RFQ_LLM_PARALLEL` (default 10), `RFQ_LLM_TIMEOUT`, `RFQ_SUBMIT_RESERVE` (s, default 330),
`RFQ_THINKING_EXTRACT=1` (slow; off by default).
