import json

from rfq.log import encode, timed


def test_long_nested_records_are_valid_bounded_json():
    line = encode("extract", {"data": [{"text": '\\"' * 9000, "unicode": "ä" * 6000}] * 200,
                              "seconds": 1.234, "count": 42})
    assert len(line) <= 4000
    decoded = json.loads(line)
    assert decoded["event"] == "extract"
    assert decoded["seconds"] == 1.234
    assert decoded["count"] == 42


def test_large_maps_are_valid_and_preserve_metrics():
    line = encode("metric", {"seconds": 2.5, **{f"large_{i}": "x" * 9000 for i in range(200)}})
    assert len(line) <= 4000
    assert json.loads(line)["seconds"] == 2.5


def test_phase_failure_is_logged_and_raised(capsys):
    try:
        with timed("probe", site="synthetic"):
            raise ValueError("bad")
    except ValueError:
        pass
    row = json.loads(capsys.readouterr().out)
    assert row["status"] == "error" and row["phase"] == "probe"
    assert row["seconds"] >= 0
