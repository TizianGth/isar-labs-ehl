# Run-local reuse and prompt reduction

Frozen base: `c93bc20` (`codex/inference-fusion`). Changes live in the separate
`codex/token-reuse` worktree. Browser implementation is unchanged.

## Measured evidence

| Offline workload | Before | After |
| --- | ---: | ---: |
| 16 representative synthetic decision requests, input characters | 128,932 | 111,627 |
| 20 selected structured sources, mocked inference requests | 8 | 1 |
| Structured workload input characters, including template learning | 27,319 | 6,107 |
| Structured workload output characters | 2,693 | 311 |
| Structured workload fresh source reads | 20 | 20 |
| Exact material rows recovered | 20 | 20 |

Decision prompt characters fell 13.4%. The structured workload input characters
fell 77.6%; the learned table template had 19 subsequent hits. These are character
counts and mocked request counts, **not metered tokens, model-quality scores, or
platform timings**. The decision comparison uses the frozen base; the structured
comparison isolates template reuse inside the changed implementation.

The reproducible genetic search evaluated 200 unique knob combinations across
nine generations with crossover, mutation, selection and exploration. It ranks
actual offline prompt sizes, synthetic read waves and extraction batch counts.
It does not establish 200 independently useful designs or predict measured seconds.
No winning candidate was automatically promoted. Defaults remain 6,500 view
characters, three sample rows, 1,200 result characters, four parallel source reads
and three sources per extraction batch.

## Implementation

- Conditional field guides/system instructions, completed-check compression,
  expandable exact result handles, and exact anchored mail-body copying reduce
  repeated input and generated text. Original fact contracts remain available.
- Concurrent source reads share one load per context/session/URL. Later reads
  reload live content; there is no blanket completed-URL freshness cache.
- Explicit model-selected table templates reuse meanings, never old row values.
  Locale, numeric notation, frame, headers, surrounding context and session guard
  reuse. Only complete model-confirmed sources with every column mapped can
  bypass extraction. Layout reuse requires explicit authorization and no fixed
  record context. Complete original source evidence stays retained.
- Explicit form reuse matches current controls uniquely, targets the chosen
  answer position and uses existing fill/readback validation. TAN/apply actions
  cannot enter reusable templates.
- Ready source groups publish extractions while slower groups load. ERP scheduling
  wakes on facts and task completion instead of one-second polling. All selected
  URLs remain accounted for; the former twelve-source cutoff is removed.
- Speculation is optional: at most two model-selected observed same-site file
  GETs, no extraction or writes. Completed bytes require strong ETag revalidation;
  absent validators require a fresh ordinary document read.
- Short budgets now admit research missions, retain a positive research window,
  bound startup retries together, and no longer add a 45-second watchdog tail.

## Validation and reproduction

Focused batch: **66 passed in 12.13 s**; leak check passed (55 values, 19 paths);
`git diff --check` passed. Tests cover fresh changed values, concurrent sharing,
frame/locale/session/context invalidation, partial-binding fallback, all 120 rows,
exact body copying, guarded form reuse, ETag changes, source streaming, event-driven
ERP completion, short-budget admission and deadline-bounded startup.

Using the repository's existing Python environment, run from the worktree root:

```sh
python dev/measure_reuse_requests.py agent dev/runs/token-reuse/requests-after.json
python dev/benchmark_reuse.py agent dev/runs/token-reuse/structured-sources.json
python dev/search_reuse_candidates.py agent dev/runs/token-reuse/search.json
python -m pytest -q -p no:cacheprovider agent/tests/test_reuse.py agent/tests/test_fused_records.py agent/tests/test_deadlines.py agent/tests/test_submit.py agent/tests/test_review.py agent/tests/test_review_regressions.py
python dev/check_no_leaks.py agent
```

To reproduce the before decision sizes, run the same measurement script against
an untouched checkout of `c93bc20`. No measurement command calls a real model.

## Remaining gate and risks

Sub-50/60-second runtime and fewer than 100k total tokens are **unproven**.
No platform run, deployment, push, submission or cancellation was performed.
The older 80.5-second/515,266-token run belongs to another package and cannot
establish these changes' quality or runtime. A one-minute phase allocation is
not a latency prediction. Browser attachment, proxy queueing, actual navigation,
provider output and signing remain unmeasured on the current package.

Unstructured/nested evidence and changed layouts fall back to inference. Smaller
views can require extra region reads. Concurrency can worsen host contention;
failed sources can fragment batches. Speculation without validators can add
reads. Exact body anchors may need correction. Synthetic tests do not prove
unseen-site model interpretation or complete-run authorization, revision,
pagination and stock-allocation quality. Full-run cached input, output, reasoning,
retries and speculation must all count toward the release token gate.

Keep the platform gate closed until a conservative critical-path forecast is
below 60 seconds, total predicted tokens are within the chosen limit, and no
local quality regression is open. Do not remove evidence to satisfy that gate.

## Follow-up: remove idle capacity and serial recovery

Navigation may now use all configured model slots when background work is absent;
background extraction retains its four-call cap and the shared global semaphore
still bounds total concurrency. This removes the former four-navigation-call
bottleneck, without enabling extra speculative model calls. Throughput improvement
depends on real proxy contention; doubling available capacity does not prove a
twofold speedup.

Commercial recovery observes distinct site instances concurrently (aliases read
once), then runs the model's selected missions concurrently on independent sites.
Missions targeting the same site remain serial. Independent observation waves
therefore change from four to one, and two independent recovery missions from
two waves to one. Evidence recovery and no-bid rechecks now respect their phase
deadline rather than extending into the submission reserve.

Follow-up focused batch: **58 passed in 13.14 s**, covering reuse, recovery,
deadlines, cancellation and hedging; leak checking and whitespace checks passed.
No real model or platform run. Existing character measurements are unchanged.

The historical 80.5-second run would need more than 20.5 seconds removed to reach
60 seconds, and at least 415,266 of its 515,266 metered tokens removed to reach
100k (80.6%). Its 140 mapping calls accounted for 288,880 tokens and 300.64 summed
request seconds; those overlapping seconds cannot be subtracted from wall time.
That older package is a diagnostic lead, not a current forecast or valid quality
baseline. Current full-run bounds remain unknown; the release gate stays closed.
