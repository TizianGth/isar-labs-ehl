"""The paired A/B decision rule of dev/eval/bench.py (synthetic runs)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("bench", Path(__file__).parents[1] / "eval" / "bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def run(cxt, cost=0.1, seconds=300, quality=100, eligible=True, failure=None, answer=True, name="r"):
    return {"run": name, "cxt": cxt, "cost": cost, "seconds": seconds, "quality": quality, "eligible": eligible,
            "failure_class": failure, "has_answer": answer}


def test_clear_win_is_adopted_and_quality_loss_rejected():
    pairs = [(run(33), run(28)), (run(34), run(30)), (run(47), run(49))]
    assert bench.decide(pairs)[0] == "ADOPT"
    pairs[1] = (run(34), run(30, quality=95))
    assert bench.decide(pairs)[0] == "REJECT"


def test_platform_failures_invalidate_and_cost_only_gains_count():
    assert bench.decide([(run(33), run(30, failure="platform"))])[0] == "INVALID"
    pairs = [(run(33, cost=0.10, seconds=330), run(32.5, cost=0.096, seconds=335)) for _ in range(3)]
    assert bench.decide(pairs)[0] == "ADOPT-COST"
    assert bench.decide([(run(30), run(33)) for _ in range(3)])[0] == "REJECT"
    assert bench.decide([(run(30), run(30.3)) for _ in range(3)])[0] == "INCONCLUSIVE"
