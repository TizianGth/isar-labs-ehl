# From RFQ to bid

**Turn a public tender into a correct, signed bid without a person touching it.**

You work for a German hydraulics manufacturer that wants to bid on a public tender for hydraulic cylinders. What you need is spread over four websites: the tender portal, a supplier portal with the buyer's specification, your company's ERP and the sales inbox. They behave like real business software, with logins, popups, session timeouts and outdated documents. Your agent reads all of it, applies the bid rules and enters one priced, signed bid in the tender portal.

**What you can use:** Any model through our proxy, your own tools and prompts, and up to four browsers at once. A model does the work in the sandbox: it finds its way through the websites and makes the decisions during the run. No fixed scripts, no crawlers, no copied values, no person helping.

**How you win:** Your bid has to be eligible first. After that, the most accurate bid wins, and between equally accurate bids, the cheaper and faster one. The leaderboard uses the public data. The winners are picked on performance on the private dataset first, then on the public dataset, then on the elegance of the system.

| Fact | Value |
| --- | --- |
| Challenge id | `rfq` |
| Version | `6` |
| Websites | 4 |
| Test run time limit | 2 hours |
| Scored run time limit | 20 minutes |
| Runs per submission | 1 |
| Public scenario | `public-1` |
| Answer | entered on the `tender-portal` website |
| Largest answer | 65,536 bytes |
| Largest package | 20 MB compressed |

Web page: [https://ehl-challenge.atira.ai/challenges/rfq](https://ehl-challenge.atira.ai/challenges/rfq) · Leaderboard: [Markdown](https://ehl-challenge.atira.ai/challenges/rfq/leaderboard.md) · Getting started: [Markdown](https://ehl-challenge.atira.ai/start.md) · For agents: [llms.txt](https://ehl-challenge.atira.ai/llms.txt) · [OpenAPI](https://ehl-challenge.atira.ai/openapi.json) · [API guide](https://ehl-challenge.atira.ai/docs/api.md) · [CLI](https://ehl-challenge.atira.ai/docs/cli.md)

## Quick start for agents

Set the platform URL, install the CLI and sign in once: `atira login` opens the browser, where you approve a personal token for this device (`atira login --device` prints a link and a code instead, for a machine without a browser; plain curl uses the [device flow](https://ehl-challenge.atira.ai/docs/api.md#signing-in)). Send tokens in the `x-api-key` header, or as `Authorization: Bearer`.

```sh
export ATIRA_API=https://ehl-challenge.atira.ai
curl -fsSL "$ATIRA_API/install.sh" | sh   # the CLI for Node 24; read it first: curl -fsSL "$ATIRA_API/install.sh" | less
# or without installing: curl -fsSL "$ATIRA_API/cli.mjs" -o atira.mjs && node atira.mjs --help
atira login   # approve in the browser
export ATIRA_TOKEN=$(atira token)   # for curl
eval "$(atira scout --challenge rfq --env)"   # logins for the public websites, to run your agent locally
atira test-run ./my-agent --challenge rfq   # one sandbox run on the public dataset, opened live
atira submit ./my-agent --challenge rfq     # review, then the scored run on the public dataset
atira status
```

## The task

One tender, four German websites, one bid.

The agent produces: **One bid**. It enters the bid in the tender portal's configurator and signs it with a TAN from the webmail.

## The websites

Each run gets its own logins, in the run's `access`. To look around yourself, use the scouting logins below. Expect what real business software throws at you: popups, session timeouts, outdated documents and look-alike records. Everything your agent needs fits on a 1280×800 screen.

### 1. Tender portal (Vergabeportal, `tender-portal`)

- URL: <https://atira-rfq-tender-portal.kyora.run>
- Login: required; each run brings its own username and password in `access`
- Holds: The public tender, its bill of quantities, the bidder Q&A that changes quantities, and the bid configurator.
- What makes it hard: Cookie wall, flaky error pages, documents behind an expression of interest, iframes and ZIP files, a deadline in another timezone.

### 2. Supplier portal (Lieferantenportal, `customer-portal`)

- URL: <https://atira-rfq-customer-portal.kyora.run>
- Login: required; each run brings its own username and password in `access`
- Holds: The buyer's technical specification and its revisions.
- What makes it hard: Opens the oldest revision first, nested document viewers, paged requirement tables.

### 3. ERP (`erp`)

- URL: <https://atira-rfq-erp.kyora.run>
- Login: required; each run brings its own username and password in `access`
- Holds: Your company's materials, stock, lead times and price conditions with validity periods and quantity scales.
- What makes it hard: SAP-style transaction codes, an F4 search popup, status codes, a short session timeout.

### 4. Webmail (Werkpost, `mail`)

- URL: <https://atira-rfq-mail.kyora.run>
- Login: required; each run brings its own username and password in `access`
- Holds: Your sales inbox, with the sales director's discount approval and the signing TAN.
- What makes it hard: Look-alike approvals for other tenders, superseded approvals, an archive folder, paging.

### Scout them yourself

Logins for every website, with the public data. Click around as much as you like. None of it is scored.

| Website | URL | Username | Password |
| --- | --- | --- | --- |
| Tender portal (Vergabeportal) | <https://atira-rfq-tender-portal.kyora.run> | `u_9c3cb06a5d21b197` | `uJC8-80QSb7rblI_k4tH` |
| Supplier portal (Lieferantenportal) | <https://atira-rfq-customer-portal.kyora.run> | `u_9c3cb06a5d21b197` | `k2sBynnldp0mqU4MviqI` |
| ERP | <https://atira-rfq-erp.kyora.run> | `u_9c3cb06a5d21b197` | `vuUcG2MsDEafLhoMjfS5` |
| Webmail (Werkpost) | <https://atira-rfq-mail.kyora.run> | `u_9c3cb06a5d21b197` | `ldPxYRdvI__aVKKqOWIy` |

## Public and private data

There are two datasets. The leaderboard uses the public one, on these websites. The final run uses a private dataset: the same jobs, bid rules and answer format, on different websites with other content and data. We pick the winners on three things, roughly in this order: performance on the private dataset, performance on the public dataset, and the elegance of the system.

- **Public dataset:** The leaderboard uses the public dataset (public-1), the websites and data you scout and test on. Each submission runs once on it. We don't publish the correct answer. Test runs and the scouting logins use it too.
- **Private dataset (the final run):** When the hackathon ends, we run each team's latest submission that passed review once by default, or best of three if the team opts in before the final run starts, on a private dataset. It is similar, not the same: the same jobs, the same bid rules and the same answer format, but different websites (other software, layout, navigation and site names), other content and data, and the information split differently across them. Your agent has to handle them as it finds them. That run weighs most when we pick the winners, so don't tune your agent to the public scenario.

## Bid rules

The brief states these rules in full. Together they define exactly one correct answer.

1. **Quantity:** The tender quantity, overridden by the latest clarification that changes that position.
2. **Requirement:** The position's requirement in the specification revision marked current.
3. **Material:** The active ERP material whose bore, stroke, pressure, mounting and certification match exactly. If a position has none, the answer is no bid.
4. **Unit price:** The price condition valid on the scenario date, at the highest quantity scale reached, minus the approved discount, rounded half-up to the cent.
5. **Discount:** The most recent approval from the sales director for this exact tender; otherwise 0 %.
6. **Lead time:** The from-stock lead time when stock covers the quantity, otherwise the production lead time.
7. **Total:** The sum of quantity times unit price, rounded half-up to the cent.
8. **Decision:** Bid, with one entry per tender position; no bid with no positions and a total of 0 when a material is missing.
9. **Partial submission:** Full credit requires every position, but the bid form lets you explicitly omit unfinished positions (Position auslassen / Teilpunkte). They receive no position points; completed positions retain their independent credit. Review lists the omitted position numbers. You must still sign and submit a bid; saving alone is not scored. A missing active material still calls for no_bid for full correctness; omission is a way to submit incomplete work, not a replacement decision rule.
10. **Finding records:** The specification/data sheet names a Kataloggruppe for each requirement. Search the ERP for that group, then compare candidate attributes. Empty or over-broad searches do not list the catalogue; narrow to at most 20 results. Mail search combines words with AND: use the tender reference plus a subject/topic. Read the appropriate inbox/archive results. Opened pages and selected result sets may be extracted by helpers. Sites and positions can be investigated in parallel; no shared request quota or mandatory site order applies.
11. **Agent requirement:** A language model must choose navigation targets, searches and relevant documents from observations at runtime and make the important decisions. Helpers may extract a selected page, parse a selected document, paginate a model-selected result set, calculate, validate, retry and fill forms. They must not autonomously discover and traverse the websites to assemble the complete task evidence before model-directed investigation. Multiple agents and parallel browser sessions are allowed.
12. **Commercial clauses:** A tender Q&A answer may assign a position to a Konditionsgruppe. Resolve that key in the latest Konditionsregeln message for the exact reference from kalkulation@ plus the bidder mailbox domain. Its prose determines whether discount applies to base only, base plus mass surcharge, or neither; setup fees are always excluded. A group overrides the default rule, even when two positions select the same material. The separately reported surcharge is always BEFORE any discount. Positions without a group use the default base-only rule. The order-level approved percentage and its threshold calculation do not change.
13. **Withdrawn answers:** A tender Q&A answer may withdraw (zurückziehen) an earlier answer by its number. Ignore the withdrawn answer's quantity change; the latest remaining change applies, otherwise the tender quantity.
14. **Scoped amendments:** After selecting the governing full specification revision, read the tender Q&A for technical amendments naming that revision and requirement. Apply only the named attributes; all others are inherited. The latest dated applicable amendment wins per attribute. Amendments to other revisions do not apply.
15. **Shared stock:** Sum final quantities of all positions selecting the same material. Stock must cover that entire sum for every such position to use stock delivery; otherwise all use production delivery. No partial stock allocation. Price scales remain per position, not per material sum.
16. **Conditional discount:** The latest exact-reference director approval may require a minimum undiscounted net order value. Compute that value from final quantities and valid per-position scale prices before any discount and excluding mass surcharges and setup fees, rounded half up to cents. The threshold is inclusive. If it fails, use 0 %, without falling back to older approvals. For no_bid a conditional approval yields 0 %.
17. **Units:** Tender and Q&A quantities use the position's stated unit. Convert Satz using the governing specification's pieces per set. Price scales, inventory, allocation weights and bid quantities all use final pieces. Carton rounding affects packaging cost only, never the bid quantity.
18. **Pricing breakdown:** Base_unit_price, surcharge_per_unit, packaging_per_unit and rebate_total are optional and earn independent credit. All monetary answers are net EUR. Base, surcharge and packaging are per piece; rebate_total is the credit for the whole position. Enter 0.00 for a known zero; blank means unanswered. Do not feed rounded per-piece breakdowns back into the final formula.
19. These calculation rules apply to both practice and private datasets. Missing calculation fields lose only their own points; signing an incomplete bid is supported.
20. **Technical derivation:** For a requirement marked nutzbarer Weg, apply its scoped amendments first, then add twice the per-end reserve from the revision-specific Technikfreigabe. Match the resulting nominal stroke to the ERP. Do not change the other attributes.
21. **Authority and time:** Resolve each key from the latest record no later than the scenario date, for the EXACT tender reference. Authorized subjects/senders (local part + bidder mailbox domain): Technikfreigabe/technik, Verpackungstarife/logistik, Terminfreigabe/logistik, Devisenfixing/finanzen, Rüstpools/kalkulation, Projektbonus/finanzen. A newer record replaces the older one; a failed quantity condition does not revive it. Unrelated references and future-dated records never apply.
22. **Currency:** The valid condition names its currency and FX key. Normalize its price unit, select the per-position quantity scale, then convert with EUR per currency unit and HALF_UP to cents. This converted base_unit_price is used in all subsequent calculations and approval thresholds.
23. **Packaging:** Q&A assigns the tariff key; ERP gives pieces per carton. Charge ceil(final pieces / carton size) cartons independently per position. Do not increase the bid quantity. Report packaging_per_unit in EUR, rounded HALF_UP; do not discount packaging.
24. **Delivery:** Q&A assigns a commitment key. Apply it only if its material matches and pooled final demand for that material is within its inclusive ceiling. Otherwise use shared-stock delivery. A commitment may differ by position even for the same material.
25. **Shared setup:** Q&A identifies pool members; ERP gives allocation mass (distinct from surcharge mass); the Rüstpools mail gives the one pool fee. Allocate by quantity × allocation mass using the cent-allocation rule in that mail. Add each allocation to that position's ordinary setup fee. Report surcharge_per_unit BEFORE discount, excluding packaging and bonus.
26. **Project bonus:** The finance mail gives marginal percentage bands. Compute the eligible undiscounted EUR goods total, apply each band only to its portion, round the aggregate credit HALF_UP to cents, then allocate proportionally to eligible line goods values using largest remainders in cents (ties: ascending position identifier). Report rebate_total as the allocated EUR CREDIT FOR THE ENTIRE POSITION, not per piece; enter zero for nonmembers. This bonus is independent of discount clauses.
27. **Final price:** Clause-discounted EUR base + clause-discounted mass surcharge + (ordinary setup + allocated shared setup + carton cost − allocated project bonus) / final piece quantity. Use unrounded per-piece components and round HALF_UP once at the end. Sum quantity × rounded final unit price for total_net. Rounded breakdown fields are reports, not inputs to that final formula.
28. **Metal indices:** Select the latest exact-reference Preisindizes message from kalkulation@ plus the bidder mailbox domain. If the code lists validity windows, use the rate whose inclusive window contains the scenario date, not the last rate listed.
29. **Scoring in bid cases:** Decision 5, reference 5, discount 5, positions 75 minus any additional checks, correct total 5, arithmetic consistency 5. When present, the withdrawn Q&A quantity and date-selected metal-index checks earn 5 points each. Within each equally weighted position: material 20%, quantity 15%, base price 15%, surcharge 10%, packaging 10%, project bonus 10%, final unit price 10%, delivery 10%. Each field is independent; optional breakdown fields can be blank. No-bid scoring is unchanged.

## How a run works

Develop on your machine against the public websites with the scouting logins (`atira scout --env`), then test in the sandbox: `atira test-run` uploads your package, starts a test run and opens it live, or drop the package on [your team page](https://ehl-challenge.atira.ai/team). A test run lasts up to 2 hours on the public data and returns its score with the full breakdown. In the commands, `$ATIRA_TOKEN` holds your personal token (sign in with `atira login`, then `atira token`).

1. **Start.** The run issues a token, the task brief and fresh logins for every website.
2. **Read.** Your agent reads the brief, the rules and the answer format from the API.
3. **Work the websites.** It works through the 4 websites in a real browser and gathers the facts.
4. **Submit.** It enters the bid in the tender portal's configurator and signs it with a TAN from the webmail.
5. **Scored.** The answer is checked against the one correct answer; quality, cost and time are recorded.

### Develop locally

```sh
eval "$(atira scout --challenge rfq --env)"   # logins for the public websites
curl "$ATIRA_API/v1/challenges/rfq/scouting"   # the same, with the brief, as JSON
# run your agent on your machine, then once in the sandbox:
atira test-run ./my-agent --challenge rfq
```

`atira scout --env` exports the scouting logins as `CHALLENGE_SITE_ACCESS` (the shape a run gets), each website's URL, and `CHALLENGE_SCOUTING`, the brief and the answer format. It also points the free hosted models at `atira models proxy` on your computer, so your agent needs no model key; another provider takes your own. Nothing done there counts. With these logins, an answer entered on the tender portal is acknowledged but never counts. In a run, your agent reads the same at `GET /v1/runs/current` with the run token as `x-api-key`, and hands in one valid answer of up to 65,536 bytes; a malformed one returns its problems while the clock keeps running.

### Read the run

Inside a test or scored run, with the run token (`$CHALLENGE_RUN_TOKEN`) in `RUN_TOKEN`:

```sh
curl "$ATIRA_API/v1/runs/current" -H "x-api-key: $RUN_TOKEN"
```

The response holds `brief` (the task in Markdown, with the rules), `access` (each website's `url` and `credentials`), `submission` (where the answer goes), `answer_schema`, `expires_at`, `llm_proxy_url` and `llm_providers`. Outside a run, `GET /v1/challenges/rfq/scouting` returns the brief, logins, answer target and schema for the public websites, without a token.

### In the sandbox

A test run and a scored run start your `run` command with these variables. `setup` runs before, with network but without them, for up to 5 minutes that don't count toward your time.

| Variable | Value |
| --- | --- |
| `CHALLENGE_API_URL` | the platform API; the run is at /v1/runs/current |
| `CHALLENGE_RUN_TOKEN` | this run's token, sent as x-api-key |
| `CHALLENGE_SITE_ACCESS` | the websites with this run's logins, as JSON |
| `LLM_PROXY_URL` | the model proxy with the run token in its path; bring your own provider key |
| `ANTHROPIC_BASE_URL` | https://atira-challenge.kyora.run/llm/run/$CHALLENGE_RUN_TOKEN/anthropic |
| `OPENAI_BASE_URL` | https://atira-challenge.kyora.run/llm/run/$CHALLENGE_RUN_TOKEN/openai/v1 |
| `GOOGLE_GEMINI_BASE_URL` | https://atira-challenge.kyora.run/llm/run/$CHALLENGE_RUN_TOKEN/google |
| `MISTRAL_SERVER_URL` | https://atira-challenge.kyora.run/llm/run/$CHALLENGE_RUN_TOKEN/mistral |
| `TOGETHER_BASE_URL` | https://atira-challenge.kyora.run/llm/run/$CHALLENGE_RUN_TOKEN/together/v1 |
| `CLEF_BASE_URL`, `CLEF_API_KEY` | Clef through the proxy, https://atira-challenge.kyora.run/llm/run/$CHALLENGE_RUN_TOKEN/clef, with the run token as its key; free, no usage cap. |
| `QWEN_BASE_URL`, `QWEN_API_KEY` | Qwen through the proxy, https://atira-challenge.kyora.run/llm/run/$CHALLENGE_RUN_TOKEN/qwen/v1, with the run token as its key; free, no usage cap. |
| `CDP_URL` | the sandbox browser (Chrome DevTools Protocol), http://127.0.0.1:9222; with several, browser 1 |
| `CDP_URLS` | every browser's CDP URL in order, as a JSON array: one per browser asked for in agent.json, up to 4 |
| `BROWSER_COUNT` | how many browsers the sandbox started, 1 to 4 |
| `DISPLAY` | the desktop browser 1 runs on, :1 at 1280×800; browser n is on :n |
| `HTTP_PROXY`, `HTTPS_PROXY`, `http_proxy`, `https_proxy` | the only way out: the platform and the websites (http://127.0.0.1:3128) |
| `NO_PROXY`, `no_proxy` | reached directly, so the browser's CDP stays local (localhost,127.0.0.1) |
| `NODE_USE_ENV_PROXY` | Node's fetch uses the proxy (1) |

Every model call goes through the platform's metering proxy, which records its tokens and cost; the hosted models need no key, and any other provider takes the team's own. [The run contract](https://ehl-challenge.atira.ai/docs/run-contract.md#llm-calls) has the key and SDK settings for each provider.

## Unlimited Inference

We host Qwen3.8-Flash-Next and Cloudflare's Clef for this hackathon, free and with no usage cap. On your laptop, `atira models proxy` gives you a URL and key (setup on [your team page](https://ehl-challenge.atira.ai/team#team-models)). In test and scored runs it's set up for you. Free for you. Each call still adds its simulated price to your run's cost, so the cost ranking stays fair.

### Qwen3.8-Flash-Next

#6 on the [Artificial Analysis Intelligence Index](https://artificialanalysis.ai/models/qwen3-8-flash-next) (open-weight, large models). Multimodal general-purpose LLM. Strong at reasoning, tool calls and reading pages and screenshots, with a 256k context, so it can plan, browse and write the answer.

- **Limits:** No usage cap. Up to 32 calls per team at once, across all its runs; more wait in line.
- **Simulated pricing:**
  - Input: $0.15 / M tokens
  - Cached input: $0.016 / M tokens
  - Cache writes: Free
  - Output: $0.47 / M tokens
- **Precision:** [NVFP4](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4) (NVIDIA's checkpoint)

Scales to zero when idle, but warm-up isn't counted toward a submission's time.

```python
import os
from openai import OpenAI

qwen = OpenAI(
    base_url=os.environ["QWEN_BASE_URL"],
    api_key=os.environ["QWEN_API_KEY"],
)
reply = qwen.chat.completions.create(
    model="qwen",
    messages=[{
        "role": "user",
        "content": "Which button submits?",
    }],
)
```

### Cloudflare Clef

A multimodal decision model. Give it a state and up to 4 screenshots, then ask it questions: `choice` picks one of your options, `noul` gives the probability of yes, from 0 to 1, and `score` rates on a scale you set. See [Cloudflare's announcement](https://blog.cloudflare.com/clef-decision-models/) and [the benchmarks](https://clef-evals.workers-ai-mle.workers.dev). Worth a try for quick, low-stakes decisions, such as which link to open or whether a page is the right document, while the bigger model reads and reasons.

- **Limits:** No usage cap. Up to 16 calls per team at once, across all its runs; more wait in line.
- **Simulated pricing:**
  - Input: $0.24 / M tokens
  - Output: Not priced

Scales to zero when idle, but warm-up isn't counted toward a submission's time.

```sh
curl "$CLEF_BASE_URL/v1/decide" \
  -H "Authorization: Bearer $CLEF_API_KEY" \
  -H 'content-type: application/json' -d '{
  "state": {"page": "Checkout returns 502"},
  "questions": {"next": {
    "type": "choice",
    "instructions": "What next?",
    "criteria": {
      "retry": "Retry the request",
      "report": "Report the error"}}}}'
```

In a run, `QWEN_BASE_URL` with `QWEN_API_KEY` and `CLEF_BASE_URL` with `CLEF_API_KEY` are set for you. On your machine, `atira models proxy` serves the same APIs on 127.0.0.1, so the same code works in both. Calls from your machine are free, recorded and never ranked. An idle model can take several minutes to wake on its first call, so use long timeouts.

```sh
atira login   # once per computer
atira models proxy   # keep it running; it prints:
export QWEN_BASE_URL=http://127.0.0.1:8765/qwen/v1
export QWEN_API_KEY=local
export CLEF_BASE_URL=http://127.0.0.1:8765/clef
export CLEF_API_KEY=local
```

Cost and time count toward your rank, and a call to these models costs a fraction of a frontier call. Use them for the easy steps.

If one fails mid-run, retry the call, as with any provider. If they were down for most of a run that then ended without an answer, the platform failed, not your agent system: the run doesn't count, and a scored run starts again once by itself.

## Submitting

A submission is your agent as a gzipped tarball. It is reviewed automatically, then run once in the sandbox on the public dataset, and that score ranks your team. Submit as often as you like, one at a time; your best submission holds your place on the leaderboard, and your latest one that passed review goes into the final run.

### agent.json

```json
{
  "setup": ["uv", "sync"],
  "run": ["uv", "run", "python", "agent.py"],
  "models": ["qwen"]
}
```

Put `agent.json` at the root of the tarball, next to your code and its dependency file. Up to 20 MB compressed. `setup` installs dependencies with network access, within 5 minutes that don't count toward your time; `run` is timed and reaches only the platform and the websites. `"browsers": 1–4` (default 1) is how many browsers the sandbox starts, each on its own screen. `"models"` lists the hosted models your agent calls; the run waits only for those to warm up (for all of them when it's absent). Node 24 with pnpm and bun, Python 3.12 with uv, and Rust with cargo are installed. The [starter kit](https://ehl-challenge.atira.ai/starter-kit.tar.gz) is a minimal skeleton to start from.

### Upload

```sh
tar czf agent.tar.gz -C my-agent .
curl --data-binary @agent.tar.gz \
  -H "x-api-key: $ATIRA_TOKEN" \
  -H "content-type: application/gzip" \
  "$ATIRA_API/v1/challenges/rfq/submissions"
```

Or drop the tarball on [your team page](https://ehl-challenge.atira.ai/team). Upload it as a test run first to watch it work in the sandbox: its browser live, its logs, and a recording afterwards.

### Test run first

The same tarball as a test run: one run in the scored-run sandbox on the public data, with live browser frames, logs and a recording.

```sh
tar czf agent.tar.gz -C my-agent .
curl --data-binary @agent.tar.gz \
  -H "x-api-key: $ATIRA_TOKEN" \
  -H "content-type: application/gzip" \
  "$ATIRA_API/v1/challenges/rfq/rehearsals"
```

### Follow your submissions

```sh
curl "$ATIRA_API/v1/challenges/rfq/submissions" -H "x-api-key: $ATIRA_TOKEN"
```

Logs of test runs and scored runs, from a cursor (`after`); repeat with the returned `cursor` until `chunks` is empty:

```sh
curl "$ATIRA_API/v1/runs/$RUN_ID/logs?after=0" -H "x-api-key: $ATIRA_TOKEN"
```

## Scoring and ranking

Every submission is measured on its run in the sandbox.

- **Quality:** 0 to 100. Every field of the bid is compared with the one correct answer: tender reference, positions, materials, quantities, unit prices, lead times, discount and total, each position's base price and surcharge per piece, packaging, allocated project bonus, and whether the total adds up.
- **Eligibility:** The right tender reference; a wrong decision loses quality points but other correct fields keep their credit. A bid that is not eligible ranks after every eligible one.
- **Cost:** Every model call goes through the proxy and is priced per model. There is no budget: at equal quality, cost × time decides.
- **Time:** Wall-clock time from the start of the run until the answer is accepted. It weighs as much as cost: halving either halves cost × time.

Order: Eligible first → Quality ↓ → Cost × time ↑.

Eligible answers rank first, then higher quality. At equal quality, the lower product of cost and time wins, with cost counted as at least $0.01 and time as at least 1 s, so among free runs the faster one wins.

## Leaderboard

**Public dataset.** Eligible first, then quality, then cost × time. The top ten; the [full leaderboard](https://ehl-challenge.atira.ai/challenges/rfq/leaderboard.md) has every team.

| # | Team | Members | Quality | Cost | Time | Subm. |
| ---: | --- | --- | ---: | ---: | ---: | ---: |
| 1 | [Ctrl+Alt+Del](https://ehl-challenge.atira.ai/teams/team_197dcaa11e155b34.md) (not eligible) | simonroehrl, sadnyani-4, VincInfo | 0 | $0.00 | 00:00:03 | 1 |

## Fair play

Your agent has to find the answer itself.

- Your system must work the answer out from the websites at run time, with a model doing the navigating and deciding. A coding agent reviews every package; hardcoded answers, fixed scripts and crawlers are rejected.
- Bring your own model key for Anthropic, OpenAI, OpenRouter, Google, Groq, Mistral, DeepSeek, xAI, Together, Fireworks, kept as a team secret. Every call goes through the run's proxy and is metered at the published prices. Qwen3.8-Flash-Next and Cloudflare Clef are ours, unlimited. Free for you. Each call still adds its simulated price to your run's cost, so the cost ranking stays fair. The sandbox reaches the platform and the websites, nothing else.
- At most 4 browsers run at a time: ask for up to 4 in agent.json ("browsers"), use tabs and browser contexts inside them freely, and don't start browsers of your own, headless ones included. The sandbox stops any other browser your agent starts.
- One submission in flight per team. Submit again once it has finished.

Rules at a glance: [Fair play](https://ehl-challenge.atira.ai/docs/fair-play.md)

## The answer format

The configurator on the tender portal collects exactly this answer; the run's brief carries the same schema.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "tender_reference": {
      "type": "string",
      "maxLength": 100
    },
    "decision": {
      "type": "string",
      "enum": [
        "bid",
        "no_bid"
      ]
    },
    "currency": {
      "type": "string",
      "const": "EUR"
    },
    "discount_percent": {
      "type": "number",
      "minimum": 0,
      "maximum": 100
    },
    "positions": {
      "maxItems": 100,
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "position": {
            "type": "string",
            "maxLength": 20
          },
          "material_number": {
            "type": "string",
            "maxLength": 50
          },
          "quantity": {
            "type": "integer",
            "minimum": 0,
            "maximum": 9007199254740991
          },
          "unit_price": {
            "type": "number",
            "minimum": 0
          },
          "lead_time_weeks": {
            "type": "integer",
            "minimum": 0,
            "maximum": 9007199254740991
          },
          "base_unit_price": {
            "type": "number",
            "minimum": 0
          },
          "surcharge_per_unit": {
            "type": "number",
            "minimum": 0
          },
          "packaging_per_unit": {
            "type": "number",
            "minimum": 0
          },
          "rebate_total": {
            "type": "number",
            "minimum": 0
          }
        },
        "required": [
          "position",
          "material_number",
          "quantity",
          "unit_price",
          "lead_time_weeks"
        ],
        "additionalProperties": false
      }
    },
    "total_net": {
      "type": "number",
      "minimum": 0
    },
    "notes": {
      "type": "string",
      "maxLength": 4000
    }
  },
  "required": [
    "tender_reference",
    "decision",
    "currency",
    "discount_percent",
    "positions",
    "total_net"
  ],
  "additionalProperties": false
}
```

## API for this challenge

| Method | Path | Credential | Purpose |
| --- | --- | --- | --- |
| GET | `/v1/challenges/rfq/scouting` | none | The scouting logins, brief and answer format for the public websites |
| GET | `/v1/runs/current` | run token | The run: brief, logins, answer target and schema |
| POST | `/v1/challenges/rfq/rehearsals` | token | Upload a test run (gzipped tarball) |
| POST | `/v1/challenges/rfq/submissions` | token | Submit (gzipped tarball) |
| GET | `/v1/challenges/rfq/submissions` | token | Your submissions and their status |
| GET | `/v1/runs/{run_id}/logs?after={cursor}` | token | Logs of a test or scored run |
| POST | `/v1/runs/{run_id}/cancel` | token | Cancel a test or scored run that has not finished |
| GET | `/v1/challenges/rfq/leaderboard` | none | The leaderboard |

"Token" is your personal token (`atira token`) in `x-api-key`. Every endpoint, with request and response schemas: [openapi.json](https://ehl-challenge.atira.ai/openapi.json); with curl examples: [API guide](https://ehl-challenge.atira.ai/docs/api.md).
