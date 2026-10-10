# Atira RFQ agent — research phase

The current implementation is the model-directed research phase. It reads the run, lets Qwen choose pages and
actions at runtime, and saves selected source content into `evidence.json`. It intentionally stops before
calculating, signing, or submitting a bid; those later phases still need to be implemented.

| File | What it does |
| --- | --- |
| `run.py` | The run contract: reads the run (`load_run`) and submits an API answer (`submit_answer`) |
| `browsers.py` | Attaches to the run's browsers over CDP and gets a page (`connect`); locally it starts a Chromium |
| `models.py` | One call to each hosted model: `ask_qwen` (Qwen3.8-Flash-Next) and `ask_clef` (Cloudflare Clef) |
| `agent.py` | The entry point: reads the run, connects browser 1, and starts runtime-directed research |
| `research.py` | Model-directed page selection, safe browser actions, DOM/frame capture, and selected PDF/Office/CSV extraction |
| `evidence.py` | Deduplicated central evidence store with source URL, site ID, timestamp and content |
| `agent.json` | How the sandbox installs (`uv sync`, the versions in `uv.lock`) and runs it; it waits for the hosted `qwen` |
| `tests/` | The plumbing against fakes |

## Research and evidence capture

The investigator sees the current rendered page and its visible links, buttons and form fields. Qwen chooses which
site, search, record or document to inspect next at runtime. When a selected page or document is opened, helpers
capture its rendered DOM text (including readable embedded frames) or extract text from selected PDF, DOCX, XLSX,
CSV and text responses. Each item is saved immediately into one `evidence.json` file with its site, URL, title,
capture time and content. `EVIDENCE_PATH` can override the output path; `RESEARCH_MAX_STEPS` controls the action limit
(default 40).

The collector does not autonomously crawl every link. This follows the challenge's fair-play rules: the model must
pick navigation targets and relevant documents from runtime observations. Helpers block bid submission, signing,
save/acknowledgement and document-access receipt actions. CAPTCHA or robot checks are not bypassed.

This is only the evidence-gathering phase. It returns without an answer, so Atira will label a run `no answer` and
quality 0 until bid reasoning, validation and the final authorized submission flow are implemented.

Mind the clock: a scored run has 20 minutes, and `run.seconds_left(task)` says how many seconds are left. Every part
of the answer scores on its own, so submit your best answer about a minute before the end rather than keep
searching; an answer that comes too late scores 0.

More browsers: set `"browsers": 2` (up to 4) in `agent.json`, then `await browsers.connect(playwright, 1)` attaches
to browser 2.

## The hosted models in short

Both are free and need no key of yours: runs get the variables, and locally `atira scout --env` sets them, pointed
at `atira models proxy`.

```python
import models

text = await models.ask_qwen("Which button submits this form? ...")          # QWEN_BASE_URL, QWEN_API_KEY
yes = await models.ask_clef({"page": page_text}, "Is this the current revision?")  # CLEF_BASE_URL, CLEF_API_KEY
```

`models.qwen()` is an OpenAI SDK client, for tool calls, images and everything else. Clef is set only where the
platform hosts it; if your agent relies on it, list `"models": ["qwen", "clef"]` in `agent.json` so the run waits for
it to warm up. Another provider works with your own key through the run's metering proxy:
`$API/docs/run-contract.md#llm-calls`.

## Run it locally

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) (open a new terminal after installing it, so
it is on your PATH) and the Atira CLI, signed in once with `atira login`.

Terminal 1: start the free models and leave it open.

```sh
atira models proxy
```

Terminal 2, in this folder:

```sh
uv sync && uv run playwright install chromium
eval "$(atira scout --env)"     # the scouting logins, and the model variables pointed at that proxy
uv run --no-sync python agent.py
```

In PowerShell on Windows, Terminal 2:

```powershell
uv sync; uv run playwright install chromium
atira scout --env | Out-String | Invoke-Expression    # the same exports
uv run --no-sync python agent.py
```

A Chromium window opens (`HEADLESS=1` hides it). Nothing done with the scouting logins counts, and locally an answer
is printed, never submitted. If `atira` says `Unknown command: models`, run `atira upgrade`.

## Test-run it, then submit it

```sh
atira test-run .   # the sandbox, exactly like a scored run, on the public dataset; opens the run page live
atira submit .     # reviewed, then run once and scored for the leaderboard
```

Both pack this directory without what `.gitignore` excludes.

## Environment

| Variable | Set by | Used for |
| --- | --- | --- |
| `CHALLENGE_API_URL`, `CHALLENGE_RUN_TOKEN` | runs | the run, at `/v1/runs/current` |
| `CHALLENGE_SCOUTING` | `atira scout --env` | locally: the brief and the scouting logins |
| `CDP_URL`, `CDP_URLS` | runs | the sandbox's browsers; locally the kit starts its own Chromium |
| `QWEN_BASE_URL`, `QWEN_API_KEY` | runs, `atira scout --env` | the hosted Qwen |
| `CLEF_BASE_URL`, `CLEF_API_KEY` | runs, `atira scout --env`, when Clef is hosted | the hosted Clef |

## Docs

- How a run works: `$API/docs/run-contract.md`
- Building an agent: `$API/docs/building-an-agent.md`
- Fair play, the rules: `$API/docs/fair-play.md`

## Tests

```sh
uv run python -m unittest discover -s tests
```
