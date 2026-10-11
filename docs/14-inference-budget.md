# Inference budget and measurement gates

Work starts from immutable commit `7de9cdab9e7ba3988970e6239e6245c1c36df34e`
on `codex/inference-fusion`. This document separates captured measurements from
engineering budgets. A budget is not evidence of achieving the target.

## Acceptance and run allocation

The same frozen candidate must achieve eligible, 100 public quality, platform
runtime strictly below 90 seconds, and at most 200,000 total tokens on both final
public runs. Aim for approximately 100,000 tokens and 40–60 seconds. Cached input,
output, reasoning, retries and metered speculative requests all count. Private
holdouts must also be correct and meet this envelope before claiming the overall
goal. Slow quality recovery remains a recorded speed or token failure.

Allocate at most 12 backend runs: one baseline, two paired development routing
runs, one second-development-world confirmation, six frozen holdouts and two
identical-package public confirmations. Run one backend candidate at a time.
The cancelled baseline consumes run 1; it does not justify an extra run. Ranked
submission and cancellation remain separate human-authorized actions.

## Captured starting point

The baseline export under `dev/runs/optimization-7de9cda/agent/` was created with
`git archive` and was not edited. Its run was `run_6eca52242817d30b`, package hash
prefix `6356931330c7`. The human cancelled it before a signed answer. Consequently
there is no valid unchanged-commit quality measurement.

The retained `get_run.json` snapshot was captured while the run was still running
at 3 minutes 11 seconds. It contains 155 calls, 343,704 **uncached** input tokens,
234,240 additional cached input tokens, 27,870 output tokens, and displayed cost
$0.0684. Thus **605,814 tokens is a lower bound**, not the final total. The later profile contains 161 calls, 5 minutes
24 seconds and displayed cost $0.0747. These snapshots have different cutoffs and
must not be combined into a fabricated final token ledger.

| Captured baseline quantity | Value | Interpretation |
|---|---:|---|
| Successful calls in early snapshot | 155 | Partial run |
| Uncached + cached input + output | 605,814 | Already exceeds the token limit |
| Cached input | 234,240 | Additional to Atira's raw input; included in SDK prompt tokens |
| Call latency p50 / p95 | 2.0 / 5.5 seconds | Observed distribution at snapshot cutoff |
| Queue wait p50 / p95 | 1.6 / 5.4 seconds | Hosted queue contributes materially |
| Maximum simultaneous calls | 7 | More concurrency alone does not remove latency |
| Longest observed inactive interval | 82.3 seconds | Platform: no input, screen change or call in flight |

The long interval at 3:15.4–4:37.7 follows a mail overlay guard. Another 20.8-second
interval occurs at 4:53.4–5:14.2. The startup interval is 19.4 seconds at
0:02.0–0:21.4, after a login guard. These associations identify instrumentation
targets; they do not prove which browser operation caused every interval. The
profile records no screen changes for any browser, so screen inactivity alone
is weak evidence here.

The saved action log contains 41 clicks, 5 typing operations, 4 pagination reads,
16 current-page extractions, 24 selected-URL extractions, 4 visual inspections,
6 form fills, 6 URL opens and 1 document read. It does not include complete
requests or source observations. An exact reconstruction of compact requests
cannot be derived from this capture. Future instrumented runs must expose actual
prompt character counts and metered tokens, rather than treating this forecast
as a reconstruction.

The separate quality reference `run_ca1fd554694cab86` used a different package:
100 quality, 149.787 seconds, $0.084641, 212 calls, 408,805 raw uncached input,
346,432 additional cached input and 37,823 output, totaling **793,060 tokens**
under the same platform normalization. The planning figure of 446,628 omitted
cached input by incorrectly reading the CLI's parentheses as a subset. Preserve that reference if the new
candidate fails any release gate. It does not establish this commit's quality.

## Proposed work reduction

Fuse navigation and sparse current-state extraction, select relevant regions
instead of sending full histories, and reuse model-defined table/form mappings
within a run. Preserve full evidence through snapshot handles. New observations
follow navigation; a model still chooses destinations and relevance. Add no
routine verifier, memory inducer, or second planning model.

One planning envelope is 40 decisions × 1,800 input tokens = 72,000 input, 8,000
total output and a **combined 20,000-token allowance for selected-document
extraction and recovery**, yielding 100,000. If selected-document extraction
alone exceeds that allowance, this envelope is invalid. A wider feasible envelope
is 60 decisions × 2,000 input, 20 selected chunks × 2,000 input, 10,000 output and
20,000 recovery = 190,000. Count mapping creation, chunks and all auxiliary calls.
Neither envelope is a measured result or permission to omit required evidence.

The time forecast requires dependency overlap and removing long browser waits.
Target evidence completion by 45 seconds and signing by 75, with 15 seconds of
headroom. Call counts divided by concurrency are not a critical-path proof:
navigation, new-source discovery, revision selection, readbacks and signing
introduce sequential dependencies. Measure those stages directly before making
a sub-90-second claim.

## Low-overhead profiling

### Normalize SDK and platform counters separately

The verified task-local CLI's `callSummary()` adds `call.input_tokens` and
`call.cache_read_tokens` into separate counters. Its `modelLine()` prints
`input in (cached cached)`, but the parenthetical cache count is **additional**
to the raw input value. Raw `llm-calls --json` records therefore normalize as:

```text
total_input = input_tokens + cache_read_tokens + cache_write_tokens
total_tokens = total_input + output_tokens
```

OpenAI-compatible SDK `usage.prompt_tokens` already includes cached input:

```text
total_tokens = usage.prompt_tokens + usage.completion_tokens
```

The development run `run_4ecb248a3470364d` confirms this distinction. An SDK event
records prompt=2,035, cached=1,920, output=63; its corresponding platform record
contains uncached input=115, cache read=1,920, output=63. Both normalize to 2,098.
Keep the runtime SDK's cached-subset validation. Never pass raw Atira input to
that interface without normalization, or sum SDK and platform representations
of the same requests.

The full development call capture contains 149 calls, 106,557 uncached input,
179,968 cache reads, zero cache writes and 14,327 output, totaling **300,852**.
The incomplete SDK log tail contains only 42 metered responses, so its smaller
sum cannot establish the run's total. The run failed without an answer at
101.316 seconds and $0.025589; it passes neither quality nor the resource gates.

### Runtime and offline instrumentation

`agent/rfq/profile.py` provides synchronous and asynchronous `span(...)` contexts,
`model_usage(...)` per provider request and `prompt_metrics(...)` character/image
counts. `RFQ_PROFILE=0` disables emission. It retains no history and creates no
polling tasks. One JSON line is emitted when a span closes, including cancellation.
Numeric counters and booleans are allowed; page content, URLs, prompts, answer
values, passwords and TANs are excluded. Developer-defined labels must not embed
source text. Keep per-row events out of the runtime.

Instrument browser attachment, readiness/guards, observation, model queue,
provider request, decision, selected-source extraction, source-binding parsing,
resolver, form/readback, TAN waiting and receipt acceptance. Record queue and
provider time separately. A request ID identifies every attempt, including a
speculative loser. A cancelled request without usage is **unknown**, not zero.
Reasoning is normally included in completion tokens; add it separately only when
the provider explicitly excludes it from output. Platform totals remain
authoritative for requests cancelled before usage is returned.

A local 10,000-span microbenchmark including JSON logging to `/dev/null` measured
11.04 microseconds per enabled span and 1.21 microseconds when disabled. At 1,000
spans this suggests roughly 11 milliseconds of local CPU/logging work. This is
not a guarantee for a slow platform stdout consumer. Keep span volume bounded
by actions and calls; use the platform profile to detect logging backpressure.

Aggregate **off the runtime path**:

```sh
python3 dev/profile_ledger.py LOG_OR_MCP_CAPTURE... --seconds 80 --cost 0.02
```

The analyzer reports all instrumented token usage, speculative tokens, unknown
attempts, per-category summed and union times, longest spans, uninstrumented
gaps, action counts and cost × time. Sum times overlap and cannot represent wall
time. Missing instrumented intervals are not proof that the agent was idle.
Incomplete captures or unknown usage cannot produce a passing token claim.
Do not combine captures from different runs; repeated attempt IDs must agree.

## Completion and generalization regressions to protect

Claude's independent Scout audit found a 100-quality result that depended on
fallback retries, rather than the compact Scout completing correctly. Its
reproduced failure mechanisms apply to this redesign:

- Detect output-length termination. Invalid truncated JSON must not repeatedly
  consume a full decision budget with an unchanged instruction.
- Only mark links offered after they are shown. Cache identical observed searches
  before launching another helper-page load; never silently cut whole query results.
- Show source-language topic keywords instead of using internal topic slugs as
  search terms or as evidence for routing facts to another site.
- Track mail coverage by folder **and query**. An archive listing, or one inbox
  search, cannot establish coverage of every folder or newest applicable record.
- Reject a premature finish without explicit remaining evidence gaps. First fact
  existence does not prove completion.
- Keep master material status distinct from stock/inspection status. Conflicting
  sourced statuses require a targeted recheck, not last-nonempty-wins merging.

Holdouts change navigation structures, evidence placement, rule combinations and
counterfactual values. Freeze versions before inspection. Once inspected, a world
is development data; later tuning against it cannot support an unseen-site claim.

## Ranking comparison and delivery

The public leaderboard snapshot used in planning reports Plite at 100 quality,
$0.02583 and 38.259 seconds, product **0.98822997 dollar-seconds**. At 90 seconds
the equal-quality cost would need to be below approximately **$0.0109803** to beat
that product. Prices, leaderboard positions and records can change; refresh them
for the final comparison. Public records do not expose competitor token counts
or establish a particular architecture. [Leaderboard](https://ehl-challenge.atira.ai/challenges/rfq/leaderboard.md)

Deliver the immutable package hash, private eval versions, field-level correctness,
platform token/cost totals, local request ledger, stage and critical-path timings,
all budget misses and every failed run. Two public repetitions provide a
confirmation, not a reliable tail-latency guarantee.
