"""Synthetic, unseen Clef navigation evaluation; never included in agent/.

Ground truth is used only AFTER the ordinary agent API returns. The harness
calls only the existing local Atira Clef proxy, serially, without retries.
Run with the project's interpreter and --out <absolute result path>.
"""
import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent"))
from rfq import clef  # noqa: E402


@dataclass(frozen=True)
class Case:
    name: str
    goal: str
    page: str
    expected: str | None


def cases() -> list[Case]:
    # These cases were prepared before any live Clef response was observed.
    # Refs, labels, paths, dates and job references are synthetic.
    locate = ("Open the next information source needed to read the current requirement document for this job. "
              "If this page already contains the requested source, or no unambiguous matching source is available, stay.")
    result = []

    def add(name, body, expected, goal=locate):
        result.append(Case(name, goal, "URL: https://heldout.synthetic.invalid/ui/work\n" + body, expected))

    add("direct_de", '[e7] link "Werbung" -> /opaque/r3\n[e2] link "Aktuelles Lastenheft" -> /opaque/k8\n[e19] link "Archiv" -> /opaque/m1', "e2")
    add("direct_en_permuted", '[e19] link "Archive" -> /u/x5\n[e31] link "Current requirement document" -> /u/s3\n[e1] link "Supplier news" -> /u/z9', "e31")
    add("direct_fr", '[e4] link "Actualités" -> /z/9\n[e12] link "Cahier des charges — version en vigueur" -> /z/3\n[e8] link "Archives" -> /z/4', "e12")
    add("revision_current_vs_old", 'Revision R4 is obsolete. Revision R7 is released and current.\n[e8] link "Requirement revision R4 (obsolete)" -> /a/72\n[e3] link "Requirement revision R7 (current)" -> /a/26', "e3")
    add("revision_reverse_order", '[e33] link "Requirement revision R7 — current, released" -> /b/91\n[e21] link "Requirement revision R4 — superseded" -> /b/14', "e33")
    add("draft_is_not_released", '[e18] link "Requirements R12 — draft, not released" -> /c/11\n[e5] link "Requirements R10 — released, current" -> /c/77\n[e40] link "Requirements R6 — archived" -> /c/46', "e5")
    add("date_de_cutoff", '[e6] link "Lastenheft gültig ab 18.03.2025" -> /d/63\n[e9] link "Lastenheft gültig ab 01.08.2025 (künftige Version)" -> /d/20', "e6", "Read the requirement document effective on 30.06.2025 (DD.MM.YYYY); do not open a future revision.")
    add("date_us_cutoff", '[e37] link "Requirement effective 08/01/2025 (en-US)" -> /e/83\n[e11] link "Requirement effective 03/18/2025 (en-US)" -> /e/21', "e11", "Read the requirement document effective on 06/30/2025 (en-US MM/DD/YYYY); do not open a future revision.")
    add("iframe_source", '[e2] link "Corporate homepage" -> /f/81\n=== frame 2: https://heldout.synthetic.invalid/embed ===\n[f2e19] link "Current requirements" -> /f/17\n[f2e8] link "Archived documents" -> /f/32', "f2e19")
    add("two_frames_different_labels", '=== frame 1: https://heldout.synthetic.invalid/help ===\n[f1e4] link "Help for downloading requirements" -> /g/12\n=== frame 3: https://heldout.synthetic.invalid/job ===\n[f3e4] link "Released requirements for current job" -> /g/45', "f3e4")
    add("opaque_refs_urls", '[e93] link "Released technical requirements" -> /ui/eyJrIjoib3BhIn0?q=m8\n[e51] link "General supplier brochure" -> /ui/eyJrIjoibm90In0?q=a2', "e93")
    add("quotation_vs_requirements", '[e18] link "Quotation template" -> /h/5\n[e64] link "Requirement document — current job" -> /h/3\n[e31] link "Commercial terms" -> /h/8', "e64")
    add("job_reference_match", '[e5] link "Requirements for job ORION" -> /i/19\n[e17] link "Requirements for job LYRA" -> /i/26', "e17", "Read requirements for job LYRA. Job ORION is unrelated.")
    add("next_result_page", 'Requirement search — page 1 of 2. No matching document on this page.\n[e5] link "Previous page" -> /j/4?p=0\n[e42] link "Next page" -> /j/7?p=2', "e42")
    add("observed_absolute_url", '[e7] link "Support" -> /k/1\n[e38] link "Current requirement document" -> https://heldout.synthetic.invalid/document/opaque-7', "e38")
    add("condition_source_navigation", '[e3] link "Stock overview" -> /l/39\n[e81] link "Current price conditions — read-only" -> /l/73\n[e9] link "Material catalogue" -> /l/22', "e81", "Open the current price-condition source to read it. Do not decide or calculate prices.")
    add("mail_read_only", '[e44] link "Message: travel expense newsletter" -> /m/81\n[e32] link "Message: project approval note for job LYRA" -> /m/63', "e32", "Read the project approval note for job LYRA. Only navigate to the message; do not make an approval decision.")
    add("already_has_requirements", 'CURRENT RELEASED REQUIREMENT DOCUMENT FOR THIS JOB\nAll requested requirement sections are displayed completely on this page.\nSection 1: technical requirements. Section 2: delivery requirements.\n[e5] link "Download identical copy" -> /n/81\n[e2] link "Support" -> /n/92', None)
    add("already_has_conditions", 'Current effective price-condition sheet is fully displayed here. Requested price-condition source has been reached.\n[e8] link "Catalogue" -> /o/18\n[e20] link "Download duplicate sheet" -> /o/40', None, "Read the current price-condition source. Stay if that source is already displayed completely.")
    add("no_matching_source", 'Current job: LYRA.\n[e3] link "Office opening hours" -> /p/13\n[e6] link "Holiday calendar" -> /p/14\n[e22] link "Travel policy" -> /p/15', None)
    add("only_unrelated_job", '[e8] link "Requirements for job ORION" -> /q/19\n[e1] link "Contact sales" -> /q/10', None, "Read requirements for job LYRA; other jobs do not match. Stay if no matching source is present.")
    add("conflicting_current_revisions", '[e7] link "Requirements R8 — marked current" -> /r/31\n[e26] link "Requirements R9 — marked current" -> /r/12\nBoth records claim to be the current revision; no dates or release authority are displayed.', None)
    add("ambiguous_duplicate_links", '[e6] link "Requirements" -> /s/81\n[e28] link "Requirements" -> /s/19\nNo revision, date, job or status information is visible.', None)
    add("button_only", '[e1] button "Open requirements"\n[e5] button "Next"', None)
    add("proposal_form_only", '[e4] input[text] "Unit price" value=""\n[e6] button "Submit proposal"\n[e7] input[text] "Signing code" value=""', None)
    add("vision_only_unknown_document", '[e12] image without text\n[e7] link "Support" -> /t/33\nThe page contains an unlabelled scanned image. No textual document identity or revision is available.', None)
    assert len(result) == 26
    return result


def public_status() -> dict:
    try:
        with urllib.request.urlopen("https://ehl-challenge.atira.ai/v1/models/status", timeout=10) as response:
            data = json.load(response)
        return {"http_status": 200, "models": [{k: m.get(k) for k in ("provider", "state", "queued", "checked_at")}
                                               for m in data.get("models", [])]}
    except urllib.error.HTTPError as error:
        return {"http_status": error.code}
    except Exception as error:
        return {"error_type": type(error).__name__}


def sanitized_error(error: Exception) -> dict:
    item = {"type": type(error).__name__}
    if isinstance(error, clef.httpx.HTTPStatusError):
        item["http_status"] = error.response.status_code
        try:
            body = error.response.json()
            message = body.get("error", {}).get("message", "")
            # Report the infrastructure cause, never token-bearing URL/errors.
            if "Cannot reach https://ehl-challenge.atira.ai" in message:
                item["cause"] = "Local proxy cannot reach https://ehl-challenge.atira.ai"
            elif message:
                item["cause"] = "Proxy rejected request; response message omitted for credential safety"
        except (ValueError, AttributeError, TypeError):
            pass
    return item


async def evaluate(out: Path, port: int = 8765) -> dict:
    os.environ["RFQ_CLEF_NAV"] = "1"
    # Intentionally restrict evaluation to the existing localhost proxy.
    base = f"http://127.0.0.1:{port}/clef"
    os.environ["CLEF_BASE_URL"] = base
    os.environ["CLEF_API_KEY"] = "local"
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "kind": "actual-clef-navigation-holdout",
              "case_count": len(cases()), "proxy": base,
              "public_model_status": public_status(), "runtime_changes": False,
              "qwen_calls": 0, "retries": 0, "calls": [], "infra_failure": None,
              "limitation": "Synthetic navigation holdout; no RFQ end-to-end score or quality guarantee. Thresholds unchanged."}
    original_decide = clef.decide
    request_evidence = {}

    async def instrument(payload, **kwargs):
        started = time.monotonic()
        request_evidence["attempted"] = True
        try:
            response = await original_decide(payload, **kwargs)
            response_answer = response.get("answers", {}).get("next", {})
            request_evidence["answer"] = {k: response_answer.get(k) for k in ("choice", "confidence", "probabilities", "type")}
            request_evidence["usage"] = response.get("usage")
            return response
        except Exception as error:
            request_evidence["error"] = sanitized_error(error)
            raise
        finally:
            request_evidence["request_s"] = round(time.monotonic() - started, 4)

    clef.decide = instrument
    try:
        for case in cases():
            request_evidence.clear()
            candidates = clef.observed_candidates(case.page)
            started = time.monotonic()
            selected = await clef.choose_navigation(case.goal, case.page, candidates,
                                                   observation_id=clef.snapshot_id(case.page))
            ref = selected.ref if selected else None
            semantic_evaluated = not request_evidence.get("error") and (
                not request_evidence.get("attempted") or bool(request_evidence.get("answer")))
            result = {"case": case.name, "expected_ref": case.expected, "selected_ref": ref,
                      "semantic_evaluated": semantic_evaluated,
                      "correct": ref == case.expected if semantic_evaluated else None,
                      "expected_abstention": case.expected is None,
                      "false_positive": (case.expected is None and ref is not None) if semantic_evaluated else None,
                      "selection_error": (case.expected is not None and ref is not None and ref != case.expected) if semantic_evaluated else None,
                      "false_negative": (case.expected is not None and ref is None) if semantic_evaluated else None,
                      "api_s": round(time.monotonic() - started, 4), "candidate_refs": [c.ref for c in candidates],
                      **request_evidence}
            report["calls"].append(result)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            if request_evidence.get("error"):
                report["infra_failure"] = request_evidence["error"]
                break  # no retry spam when the infrastructure is unavailable
            if request_evidence.get("attempted") and not request_evidence.get("answer"):
                report["infra_failure"] = {"type": "TimeoutOrInvalidResponse", "cause": "2s agent fast-path budget expired or response absent"}
                break
    finally:
        clef.decide = original_decide
    evaluated = [r for r in report["calls"] if r["semantic_evaluated"]]
    # A transport failure is not a semantically evaluated model decision.
    times = [r["request_s"] for r in evaluated if r.get("attempted") and r.get("request_s") is not None]
    report["summary"] = {"completed_cases": len(report["calls"]), "semantically_evaluated_cases": len(evaluated),
                         "not_run_cases": len(cases()) - len(report["calls"]),
                         "actual_requests": sum(bool(r.get("attempted")) for r in report["calls"]),
                         "correct": sum(r["correct"] for r in evaluated),
                         "accuracy": sum(r["correct"] for r in evaluated) / len(evaluated) if evaluated else None,
                         "false_positives": sum(r["false_positive"] for r in evaluated),
                         "selection_errors": sum(r["selection_error"] for r in evaluated),
                         "false_negatives": sum(r["false_negative"] for r in evaluated),
                         "latency_p50_s": statistics.median(times) if times else None,
                         "latency_max_s": max(times) if times else None,
                         "approved_for_enablement": False}
    if report["infra_failure"]:
        report["verdict"] = "Blocked by infrastructure; Clef semantic quality remains unmeasured. Keep runtime flag off."
    else:
        report["verdict"] = "Holdout complete; inspect accepted decisions and abstentions before considering enablement."
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--proxy-port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.proxy_port <= 65535:
        parser.error("invalid proxy port")
    result = asyncio.run(evaluate(args.out, args.proxy_port))
    print(json.dumps({"summary": result["summary"], "infra_failure": result["infra_failure"], "verdict": result["verdict"]}))
