"""Build a test package of agent/ with environment flags set in its agent.json, for A/B test runs.

    uv run --project agent python dev/eval/make_variant.py NAME RFQ_SOME_FLAG=1 [RFQ_OTHER=0 ...]
    atira test-run dev/variants/NAME

The copy lives in dev/variants/ (git-ignored). Its run command becomes ["env", "FLAG=value", ..., <original run>], so
the variant differs from agent/ only by those flags. Never submit a variant: submissions are made from agent/ only,
after a human approved them (AGENTS.md rule 6).
"""
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IGNORE = shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "*.pyc")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    name, flags = sys.argv[1], sys.argv[2:]
    if not re.fullmatch(r"[\w.-]+", name) or not all(re.fullmatch(r"RFQ_[A-Z0-9_]+=[\w.-]*", f) for f in flags):
        print("NAME must be a plain word and every flag RFQ_NAME=value")
        return 2
    target = ROOT / "dev" / "variants" / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(ROOT / "agent", target, ignore=IGNORE)
    manifest = json.loads((target / "agent.json").read_text())
    run = [part for part in manifest["run"]]
    if run and run[0] == "env":                      # never stack variants
        run = [part for part in run[1:] if "=" not in part]
    manifest["run"] = ["env", *flags, *run]
    (target / "agent.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (target / "VARIANT.txt").write_text("flags: " + " ".join(flags) + "\n")
    print(f"{target.relative_to(ROOT)}: run = {manifest['run']}")
    print(f"next: atira test-run {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
