# Speed and quality validation

Work is isolated on `codex/speed-quality`, based on main `0d47065`. Never upload the repository root.

Runtime changes:

- Brief analysis overlaps independent portal starts. A mission waits only for its own portal.
- Batch filling stops on every field, apply and transition error; readback catches scripts reverting values.
- Browser-context session recovery uses a shared barrier and epoch; stale tabs cannot write after a reset.
- Review contradictions always block signing, including clearly read breakdown and delivery values. Missing or
  ambiguous optional readback is reported separately. Final buttons cannot bypass the checked submission tool.
- Actual Qwen requests, including hedge twins, have a global ten-slot cap. Extraction occupies at most eight.
- JSON telemetry stays valid when compacted; request queue, provider, browser and job timing are separate.
- Revision association, unknown certification, missing FX and conflicting valid prices remain explicit gaps.
  Partial pages do not erase proven facts. Late dependencies are reconciled before pricing.

Experiments remain disabled by default:

| Flag | Behavior | Validation required |
| --- | --- | --- |
| `RFQ_ASYNC_EXTRACT=1` | Frozen model-selected snapshots are extracted while navigation continues. Failed and cancelled jobs remain incomplete; calculation waits for selected jobs. | Exact-package platform ablation and synthetic UI matrix |
| `RFQ_FORM_PLANS=1` | Model-selected semantic field bindings within this run; values come from computed positions. Plans never click transitions. Changed options, form, position or session cause fallback. | Fresh browser regression and platform ablation; no extra roundtrip saving claimed |
| `RFQ_CLEF_NAV=1` | Optional hosted Clef chooses observed read links; uncertainty falls back to Qwen. Every resulting page returns to Qwen. Forms, signing and facts remain Qwen work. | Held-out navigation decisions and separate platform experiment; add `clef` to agent.json models for warm-up |

The baseline is three serial official tests of the same frozen main package: quality100/eligible/platform-accepted
in 523.066, 428.553 and352.427seconds; mean434.682seconds. Atira ends the model token after browser acceptance, so
an expired token during the agent's subsequent receipt-reading call is not itself evidence of a failed submission.
The report records platform acceptance separately from an agent-readable receipt ID.

## Commands

```bash
make check
make fake-browser
# Existing Atira proxy; no cloud submission, six sequential synthetic runs:
make fake-matrix FAKE_PROXY=http://127.0.0.1:8766/qwen/v1
python3 dev/eval/atira_series.py --agent ./agent --out ./dev/runs/speed-baseline --label Hamburg-baseline --count 3
python3 dev/eval/atira_series.py --agent ./agent --out ./dev/runs/speed-final --label Hamburg-final --count 10
python3 dev/eval/profile_jsonl.py ./dev/runs/speed-final/01/run_logs.json --out ./dev/runs/speed-final/01/timing.json
```

`atira_series.py` copies one immutable package and runs it serially. It saves every failure, score breakdown,
platform acceptance, duration, cost, cost×time and model/browser logs. An ambiguous upload is never retried
automatically: recover the run by its persisted label. `--resume RUN_ID` attaches an already started first run.

Release gate: ten consecutive tests of the same package, eachquality100, eligible and confirmed by the platform
or a receipt, with mean≤300seconds. No failures are removed. Five minutes is a measurement target, never a shorter
abort deadline. Synthetic UI tests and recorded evidence replays do not prove private-dataset quality.

Timing sums are inclusive and may overlap. Use mission boundaries to understand the critical path; never subtract
summed parallel queue wait from wall time. The six fake styles remain developer-only and never enter the upload.
