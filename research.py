"""Model-directed website research with source-labelled capture into evidence.json.

This is deliberately not a crawler: Qwen chooses the next website and action from the current page. The
helper only captures the selected page/document, and safety checks prevent acknowledgement or bid actions.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
import zipfile
from xml.etree import ElementTree
from typing import Any
from urllib.parse import urlparse

from evidence import EvidenceStore
import models

MAX_PAGE_CHARS = 18_000
MAX_ELEMENTS = 100
MAX_DOCUMENT_BYTES = 24 * 1024 * 1024
BLOCKED_ACTION_TEXT = re.compile(
    r"\b(submit|send bid|place bid|save|sign|signieren|unterschreiben|"
    r"absenden|einreichen|speichern|bestätigen|zustimmen|accept all|"
    r"receipt|quittung|leseberechtigung|dok[uü]mentenüberlassung)\b", re.I
)


def _document_text(data: bytes, content_type: str, url: str) -> tuple[str, str] | None:
    """Extract readable text from a document the model explicitly opened; never crawl file links."""
    mime = content_type.lower().split(";", 1)[0].strip()
    path = urlparse(url).path.lower()
    if mime == "application/pdf" or path.endswith(".pdf") or data.startswith(b"%PDF-"):
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages), "pdf"
        except Exception as error:
            return f"PDF opened but text extraction failed: {type(error).__name__}: {error}", "pdf"
    if mime in {"text/csv", "application/csv"} or path.endswith(".csv"):
        try:
            return "\n".join(",".join(row) for row in csv.reader(io.StringIO(data.decode("utf-8-sig")))), "csv"
        except Exception:
            return data.decode("utf-8", errors="replace"), "csv"
    if mime.startswith("text/") or path.endswith((".txt", ".md")):
        return data.decode("utf-8", errors="replace"), "text"
    if mime in {"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"} or path.endswith((".docx", ".xlsx")):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if "word/document.xml" in archive.namelist():
                    root = ElementTree.fromstring(archive.read("word/document.xml"))
                    return "\n".join(text.text or "" for text in root.iter()
                                     if text.tag.endswith("}t")), "docx"
                shared: list[str] = []
                if "xl/sharedStrings.xml" in archive.namelist():
                    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                    shared = ["".join(node.text or "" for node in item.iter() if node.tag.endswith("}t"))
                              for item in root if item.tag.endswith("}si")]
                chunks = []
                for name in sorted(n for n in archive.namelist()
                                   if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")):
                    root = ElementTree.fromstring(archive.read(name))
                    for row in root.iter():
                        if row.tag.endswith("}row"):
                            values = []
                            for cell in row:
                                if not cell.tag.endswith("}c"):
                                    continue
                                value = next((child.text for child in cell if child.tag.endswith("}v")), "") or ""
                                if cell.get("t") == "s" and value.isdigit() and int(value) < len(shared):
                                    value = shared[int(value)]
                                values.append(value)
                            chunks.append("\t".join(values))
                return "\n".join(chunks), "xlsx"
        except Exception as error:
            return f"Office document opened but text extraction failed: {type(error).__name__}: {error}", "office"
    return None


def _site_map(task: dict) -> dict[str, dict]:
    return {item.get("siteId", "unknown"): item for item in task.get("access", []) if item.get("url")}


async def _observation(page) -> dict[str, Any]:
    """Read rendered DOM text and visible semantic controls (no OCR or screenshot interpretation)."""
    title = await page.title()
    url = page.url
    try:
        content = await page.locator("body").inner_text(timeout=5_000)
    except Exception:
        content = ""
    frames: list[dict[str, str]] = []
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            frame_text = await frame.locator("body").inner_text(timeout=1_000)
            if frame_text.strip():
                frames.append({"url": frame.url, "text": frame_text[:5_000]})
        except Exception:
            pass
    try:
        elements = await page.evaluate("""(limit) => {
          const visible = (e) => {
            const r = e.getBoundingClientRect(), s = getComputedStyle(e);
            return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
          };
          const nodes = [...document.querySelectorAll('a,button,input,textarea,select,[role=button],[role=link]')]
            .filter(visible).slice(0, limit);
          return nodes.map((e, index) => ({
            index, tag: e.tagName.toLowerCase(),
            text: (e.innerText || e.getAttribute('aria-label') || e.getAttribute('title') || '').trim().slice(0, 220),
            href: e.tagName === 'A' ? e.href : '',
            label: e.labels ? [...e.labels].map(x => x.innerText.trim()).join(' ') : '',
            placeholder: e.getAttribute('placeholder') || '',
            type: e.getAttribute('type') || '',
            role: e.getAttribute('role') || '',
            disabled: Boolean(e.disabled)
          }));
        }""", MAX_ELEMENTS)
    except Exception:
        elements = []
    return {"title": title, "url": url, "text": content[:MAX_PAGE_CHARS], "frames": frames,
            "elements": elements}


async def _login(page, site: dict) -> None:
    """Fill obvious login fields with this run's credentials after the model selected the login action."""
    credentials = site.get("credentials") or {}
    username = credentials.get("username") or credentials.get("user")
    password = credentials.get("password")
    if not username or not password:
        raise RuntimeError("Selected site's run context has no username/password")
    password_field = page.locator('input[type="password"]').first
    await password_field.wait_for(state="visible", timeout=8_000)
    user_field = page.locator('input:not([type="password"]):visible').first
    await user_field.fill(str(username))
    await password_field.fill(str(password))
    submit = page.locator('button[type="submit"], input[type="submit"]').first
    if await submit.count():
        await submit.click()
    else:
        buttons = page.get_by_role("button")
        clicked = False
        for index in range(await buttons.count()):
            button = buttons.nth(index)
            name = (await button.inner_text()).strip()
            if re.search(r"anmeld|log\s*in|sign\s*in|weiter", name, re.I):
                await button.click()
                clicked = True
                break
        if not clicked:
            await password_field.press("Enter")
    await page.wait_for_load_state("domcontentloaded", timeout=20_000)


def _parse_action(reply: str) -> dict:
    decoder = json.JSONDecoder()
    start = reply.find("{")
    if start < 0:
        raise ValueError("Model response did not contain a JSON object")
    action, _ = decoder.raw_decode(reply[start:])
    if not isinstance(action, dict):
        raise ValueError("Model action must be a JSON object")
    return action


async def _perform(action: dict, page, sites: dict[str, dict]):
    kind = action.get("action")
    if kind == "finish":
        return page, True
    if kind == "open_site":
        site = sites.get(str(action.get("site_id", "")))
        if not site:
            raise ValueError("Model selected a site_id that is not in the run access list")
        await page.goto(site["url"], wait_until="domcontentloaded", timeout=45_000)
        return page, False
    if kind == "login":
        site = sites.get(str(action.get("site_id", "")))
        if not site or urlparse(page.url).hostname != urlparse(site["url"]).hostname:
            raise ValueError("Login action must name the site currently open in the browser")
        await _login(page, site)
        return page, False
    if kind in {"click", "fill"}:
        index = action.get("element")
        if not isinstance(index, int) or not 0 <= index < MAX_ELEMENTS:
            raise ValueError("Action needs a visible element index from the current observation")
        current = await _observation(page)
        elements = current["elements"]
        if index >= len(elements):
            raise ValueError("Element index is no longer present; observe the page again")
        element = elements[index]
        target_text = " ".join(str(element.get(key, "")) for key in ("text", "label", "href"))
        if BLOCKED_ACTION_TEXT.search(target_text):
            raise ValueError("Safety check blocked an acknowledgement, save, sign, or bid action")
        # Recreate the same filtered visible element list used by _observation.
        selector = 'a,button,input,textarea,select,[role="button"],[role="link"]'
        visible_nodes = page.locator(selector)
        visible_index = -1
        chosen = None
        for dom_index in range(await visible_nodes.count()):
            node = visible_nodes.nth(dom_index)
            try:
                if not await node.is_visible():
                    continue
            except Exception:
                continue
            visible_index += 1
            if visible_index == index:
                chosen = node
                break
        if chosen is None:
            raise ValueError("Selected element is no longer visible")
        if kind == "fill":
            if element.get("type") == "password":
                raise ValueError("Passwords can only be filled by the dedicated login action")
            value = action.get("value")
            if not isinstance(value, str) or len(value) > 500:
                raise ValueError("Fill action needs a short string value")
            await chosen.fill(value)
        else:
            href = element.get("href") or ""
            if href:
                allowed_hosts = {urlparse(site["url"]).hostname for site in sites.values()}
                if urlparse(href).hostname not in allowed_hosts:
                    raise ValueError("Blocked navigation outside the challenge websites")
            await chosen.click(timeout=10_000)
            await page.wait_for_load_state("domcontentloaded", timeout=12_000)
        return page, False
    if kind == "scroll":
        direction = -700 if action.get("direction") == "up" else 700
        await page.evaluate("amount => window.scrollBy(0, amount)", direction)
        return page, False
    if kind == "wait":
        await page.wait_for_timeout(min(max(int(action.get("seconds", 2)), 1), 5) * 1000)
        return page, False
    raise ValueError(f"Unsupported model action: {kind!r}")


async def collect(task: dict, page, *, max_steps: int | None = None) -> EvidenceStore:
    """Collect pages/documents as Qwen selects them; write each source to a central JSON evidence file."""
    sites = _site_map(task)
    store = EvidenceStore()
    max_steps = max_steps or int(os.environ.get("RESEARCH_MAX_STEPS", "40"))
    brief = str(task.get("brief", ""))[:18_000]
    seen_pages: set[int] = set()

    async def capture_response(response):
        host = urlparse(response.url).hostname
        site_id = next((key for key, site in sites.items() if urlparse(site["url"]).hostname == host), None)
        if not site_id:
            return
        try:
            headers = await response.all_headers()
            content_type = headers.get("content-type", "")
            disposition = headers.get("content-disposition", "")
            interesting = ("pdf" in content_type.lower() or "spreadsheet" in content_type.lower()
                          or "wordprocessingml" in content_type.lower() or "text/csv" in content_type.lower()
                          or "attachment" in disposition.lower() or urlparse(response.url).path.lower().endswith(
                              (".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md")))
            if not interesting:
                return
            size = int(headers.get("content-length", "0") or 0)
            if size > MAX_DOCUMENT_BYTES:
                print(f"Skipped selected document over {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB: {response.url}",
                      flush=True)
                return
            body = await response.body()
            if len(body) > MAX_DOCUMENT_BYTES:
                return
            extracted = _document_text(body, content_type, response.url)
            if extracted:
                content, kind = extracted
                title = disposition.split("filename=")[-1].strip('" ') if "filename=" in disposition else \
                    urlparse(response.url).path.rsplit("/", 1)[-1] or response.url
                if store.add(site_id=site_id, url=response.url, title=title, content=content,
                             kind=kind, metadata={"content_type": content_type, "bytes": len(body)}):
                    print(f"Captured selected {kind} document from {site_id}: {title}", flush=True)
        except Exception as error:
            print(f"Could not extract selected document {response.url}: {type(error).__name__}: {error}", flush=True)

    def attach_response_capture(target_page):
        marker = id(target_page)
        if marker not in seen_pages:
            target_page.on("response", lambda response: asyncio.create_task(capture_response(response)))
            seen_pages.add(marker)

    attach_response_capture(page)
    print(f"Research started: {len(sites)} run-provided sites; evidence file={store.path}", flush=True)
    for step in range(max_steps):
        if task.get("expires_at") and _seconds_left(task) < 90:
            print("Research stopped with under 90 seconds left in the run.", flush=True)
            break
        observed = await _observation(page)
        site_id = _site_id_for_url(observed["url"], sites)
        if site_id and (observed["text"].strip() or observed["frames"]):
            page_text = observed["text"]
            for frame in observed["frames"]:
                page_text += f"\n\n[Embedded frame {frame['url']}]\n{frame['text']}"
            store.add(site_id=site_id, url=observed["url"], title=observed["title"], content=page_text,
                      metadata={"visible_controls": observed["elements"]})
        print(f"Research step {step + 1}/{max_steps}: {site_id or 'not on a challenge site'}; "
              f"{observed['title'][:100]!r}; evidence={len(store.records)}", flush=True)
        decision_context = {
            "step": step + 1,
            "available_sites": [{"site_id": key, "url": value["url"]} for key, value in sites.items()],
            "current_site_id": site_id,
            "current_page": observed,
            "evidence_index": store.manifest(),
        }
        prompt = f"""You are the runtime investigator for a German public-procurement RFQ. Use only the current run's websites and visible page evidence.
Challenge instructions:
{brief}

Choose the single next action that advances evidence collection. The website layout, names, records and values may change in another run: infer targets from this observation, do not assume fixed pages or values. The helper captures the current rendered DOM and embedded readable frames into a shared source-labelled evidence file after each step. Inspect relevant records across the sites, including current tender/Q&A, governing specification, ERP material/price/stock, and exact-reference mail records, as applicable. Do not crawl links autonomously; select each next target from this observation.

Safety: this is research only. Never submit, save, sign, create a bid, acknowledge receipt/document access, accept a legal/business condition, or bypass a CAPTCHA/robot check. You may log in with the current run credentials when a login form is visible. Do not include or repeat credentials in the action. If a gated page asks for acknowledgment, stop that branch and choose another source. When enough relevant evidence has been collected or further access would require an irreversible action, choose finish.

Return exactly one JSON object and no markdown. Actions:
{{"action":"open_site","site_id":"one id from available_sites","reason":"short"}}
{{"action":"login","site_id":"currently open site id","reason":"short"}}
{{"action":"click","element":0,"reason":"short"}}
{{"action":"fill","element":0,"value":"short search query","reason":"short"}}
{{"action":"scroll","direction":"down","reason":"short"}}
{{"action":"wait","seconds":2,"reason":"short"}}
{{"action":"finish","reason":"short"}}
Never click controls whose text indicates saving, submitting, signing, acknowledging, requesting access, or accepting. The code will block these too.

State:
{json.dumps(decision_context, ensure_ascii=False)}
"""
        try:
            action = _parse_action(await models.ask_qwen(prompt))
            print(f"Research decision: {action.get('action')} — {str(action.get('reason', ''))[:180]}", flush=True)
            page, finished = await _perform(action, page, sites)
            # A model-selected document link may open in a new tab. Follow that selected tab and capture its files.
            if page.context.pages:
                page = page.context.pages[-1]
                attach_response_capture(page)
            if finished:
                break
        except Exception as error:
            print(f"Research action failed; re-observing page: {type(error).__name__}: {error}", flush=True)
            await page.wait_for_timeout(500)
    store.save()
    print(f"Research complete: {len(store.records)} evidence records saved to {store.path}", flush=True)
    return store


def _site_id_for_url(url: str, sites: dict[str, dict]) -> str | None:
    host = urlparse(url).hostname
    return next((key for key, site in sites.items() if urlparse(site["url"]).hostname == host), None)


def _seconds_left(task: dict) -> float:
    import time
    expires_at = task.get("expires_at")
    return float("inf") if not expires_at else float(expires_at) / 1000 - time.time()
