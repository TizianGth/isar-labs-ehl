"""Replay the evidence of recorded fake-portal runs through the CURRENT rules and score the answers.

No browser, no model: what the agent found is fixed, only the rules (agent/rfq/rules) are evaluated again. The
scorer is the fake-portal harness's own `scoring.py` (independent of our resolver). A run whose evidence was
incomplete stays low here; that is an exploration problem, not a rules problem.

    cd agent && uv run --no-sync python ../dev/eval/replay_fake_portals.py [--portals <fake-portals dir>]
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

DEFAULT = Path.home() / "Documents/Codex/2026-10-10/s/outputs/fake-portals"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))

from rfq.agent.orchestrator import Blackboard, Orchestrator  # noqa: E402
from rfq.log import log  # noqa: E402,F401  (the resolver logs its trace through it)


def resolve(output: dict) -> dict:
    o = Orchestrator.__new__(Orchestrator)
    o.brief, o.bb = output["brief"], Blackboard()
    o.bb.load(output["evidence"])
    o.erp_complete = set(output.get("erp_complete") or [])
    return Orchestrator.resolve(o).answer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--portals", type=Path, default=DEFAULT)
    parser.add_argument("--out", type=Path, help="Save every replay, regression and immutable input hash as JSON")
    args = parser.parse_args()
    sys.path.insert(0, str(args.portals))
    import scoring  # the harness's independent scorer

    rows, details = [], []
    for run in sorted((args.portals / "runs").iterdir()):
        match = re.match(r"\d{8}-\d{6}-(.+)-(classic|modern|legacy|workbench|scanned|busy-office)$", run.name)
        output_file = run / "agent-output.json"
        if not match or not output_file.exists():
            continue
        case, profile = match.groups()
        captured = output_file.read_bytes()
        output = json.loads(captured)
        recorded = scoring.evaluate_answer(output["answer"], case)
        detail = {"run": run.name, "input_sha256": hashlib.sha256(captured).hexdigest(),
                  "recorded_quality": recorded.get("quality"), "has_positions": bool(output.get("evidence", {}).get("positions"))}
        details.append(detail)
        try:
            answer = resolve(output)
            now = scoring.evaluate_answer(answer, case)
        except Exception as error:                     # a crash of the rules is a finding too
            detail.update(error=f"{type(error).__name__}: {error}", replay_ok=False)
            rows.append((run.name, recorded.get("quality"), f"CRASH {type(error).__name__}: {error}", []))
            continue
        wrong = [c["field"] for c in now.get("checks", []) if not c.get("ok")]
        detail.update(quality=now.get("quality"), failed_fields=wrong,
                      replay_ok=now.get("quality", -1) >= recorded.get("quality", 0))
        rows.append((run.name, recorded.get("quality"), now.get("quality"), wrong))
    print(f"{'run':48} {'recorded':>8} {'now':>8}  fields still wrong")
    for name, before, after, wrong in rows:
        print(f"{name:48} {before!s:>8} {after!s:>8}  {', '.join(wrong[:8])}")
    report = {"kind": "fixed recorded evidence, synthetic developer score; not browser or private quality",
              "runs": details, "regressions": [r for r in details if not r.get("replay_ok")],
              "all_passed": bool(details) and all(r.get("replay_ok") for r in details)}
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
