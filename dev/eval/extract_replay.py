"""Offline replay of extraction calls on saved public pages: does a prompt/schema variant extract the same facts?

Needs the local model proxy (free, never ranked): `atira models proxy` in another terminal, then:

    uv run --project agent python dev/eval/extract_replay.py run --variant base --reps 3
    uv run --project agent python dev/eval/extract_replay.py run --variant trim --reps 3   # RFQ_SCHEMA_TRIM=1
    uv run --project agent python dev/eval/extract_replay.py compare base trim

`run` serializes every mapped fixture with the agent's own observe.js, sends the agent's own extraction prompt
(rfq.agent.orchestrator.extraction_prompt) and stores the JSON plus output tokens in dev/runs/replay/<variant>/.
`compare` reports, per kind, how often each variant's decision-relevant fields equal the base variant's most common
answer (base vs. itself = noise floor) and the mean output tokens. Decision rule (docs/13, X-4): a variant is
acceptable for a kind only if its agreement is at least the base's self-agreement (no worse than the noise floor) and
it needs at least 15 % fewer output tokens.
"""
import argparse
import asyncio
import collections
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent"))
os.environ.setdefault("QWEN_BASE_URL", "http://127.0.0.1:8765/qwen/v1")
os.environ.setdefault("QWEN_API_KEY", "local")

FIXTURES = ROOT / "dev" / "fixtures" / "public-1"
OUT = ROOT / "dev" / "runs" / "replay"
OBSERVE_JS = (ROOT / "agent" / "rfq" / "browser" / "observe.js").read_text()
# fixture name prefix -> extraction kind (pages that are not data pages are left out)
KIND_OF = [("erp_mm03_planning", "materials"), ("erp_mm03_technical", "materials"), ("erp_mmbe_", "materials"),
           ("erp_vk13_", "materials"), ("erp_search_catalog", "materials"), ("mail_msg_", "mail_message"),
           ("mail_search_", "mail_list"), ("supplier_spec_viewer_", "requirements"),
           ("supplier_spec_revisions", "spec_revisions"), ("tender_qa_content_", "clarifications"),
           ("tender_doc_positions", "positions")]
# fields the rules use (prose copies such as description/body/original_text/answer are left out)
IGNORED = {"description", "body", "quote", "original_text", "question", "answer", "short_text", "notes", "title"}


def kind_of(name: str) -> str | None:
    return next((kind for prefix, kind in KIND_OF if name.startswith(prefix)), None)


def relevant(value):
    if isinstance(value, dict):
        return {k: relevant(v) for k, v in sorted(value.items()) if k not in IGNORED and v not in (None, "", [], {})}
    if isinstance(value, list):
        return [relevant(v) for v in value]
    return str(value).strip().lower() if value is not None else None


async def serialize_all() -> dict[str, str]:
    from playwright.async_api import async_playwright
    texts = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        for path in sorted(FIXTURES.glob("*.html")):
            if not kind_of(path.name):
                continue
            await page.set_content(path.read_text(), wait_until="domcontentloaded")
            await page.wait_for_timeout(1300)
            texts[path.name] = (await page.evaluate(OBSERVE_JS, {"prefix": "e"}))["text"]
        await browser.close()
    return texts


async def run(variant: str, reps: int, reference: str, scenario: str) -> None:
    os.environ["RFQ_SCHEMA_TRIM"] = "1" if variant == "trim" else "0"
    from rfq import flags, llm
    from rfq.agent import prompts
    from rfq.agent.orchestrator import extraction_prompt
    flags.SCHEMA_TRIM = variant == "trim"
    kinds = prompts.kinds() if hasattr(prompts, "kinds") else prompts.KINDS
    texts = await serialize_all()
    target = OUT / variant
    target.mkdir(parents=True, exist_ok=True)
    async def one(name: str, text: str, rep: int) -> None:
        kind = kind_of(name)
        reply = await llm.chat([{"role": "system", "content": prompts.EXTRACT_SYSTEM},
                                {"role": "user", "content": extraction_prompt(kind, kinds[kind], text,
                                 f"https://fixture.invalid/{name}", reference, scenario)}],
                               json_mode=True, temperature=0, tag=f"replay:{kind}", timeout=120)
        data = llm._loads(reply["content"]) or {}
        tokens = reply.get("output_tokens") or len(reply["content"]) // 4
        (target / f"{name}.{rep}.json").write_text(json.dumps(
            {"kind": kind, "output_tokens": tokens, "data": data}, ensure_ascii=False, indent=1))
        print(f"{variant} {name} rep {rep}: ~{tokens} output tokens")
    await asyncio.gather(*(one(name, text, rep) for name, text in texts.items() for rep in range(reps)))


def compare(base: str, variant: str) -> None:
    def load(name):
        found = collections.defaultdict(list)
        for path in sorted((OUT / name).glob("*.json")):
            record = json.loads(path.read_text())
            found[path.name.rsplit(".", 2)[0]].append(record)
        return found
    a, b = load(base), load(variant)
    rows = collections.defaultdict(lambda: {"pages": 0, "base_self": 0, "base_reps": 0, "agree": 0, "reps": 0,
                                            "base_tokens": [], "variant_tokens": []})
    for page, records in a.items():
        kind = records[0]["kind"]
        canon = [json.dumps(relevant(r["data"]), sort_keys=True) for r in records]
        majority = collections.Counter(canon).most_common(1)[0][0]
        row = rows[kind]
        row["pages"] += 1
        row["base_self"] += sum(c == majority for c in canon)
        row["base_reps"] += len(canon)
        row["base_tokens"] += [r["output_tokens"] for r in records]
        for r in b.get(page, []):
            row["agree"] += json.dumps(relevant(r["data"]), sort_keys=True) == majority
            row["reps"] += 1
            row["variant_tokens"].append(r["output_tokens"])
    print(f"| kind | pages | base self-agreement | {variant} agreement | base out tokens | {variant} out tokens |")
    print("|---|---|---|---|---|---|")
    for kind, row in sorted(rows.items()):
        mean = lambda xs: round(sum(xs) / len(xs)) if xs else None
        print(f"| {kind} | {row['pages']} | {row['base_self']}/{row['base_reps']} | {row['agree']}/{row['reps']} "
              f"| {mean(row['base_tokens'])} | {mean(row['variant_tokens'])} |")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--variant", choices=["base", "trim"], default="base")
    r.add_argument("--reps", type=int, default=3)
    r.add_argument("--reference", default=os.environ.get("REPLAY_REFERENCE", "unknown"),
                   help="tender reference for the task context (dev only; never written into agent/)")
    r.add_argument("--scenario", default=os.environ.get("REPLAY_SCENARIO", "unknown"))
    c = sub.add_parser("compare")
    c.add_argument("base")
    c.add_argument("variant")
    args = parser.parse_args()
    if args.command == "run":
        asyncio.run(run(args.variant, args.reps, args.reference, args.scenario))
    else:
        compare(args.base, args.variant)
    return 0


if __name__ == "__main__":
    sys.exit(main())
