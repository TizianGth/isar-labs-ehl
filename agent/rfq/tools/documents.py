"""Read a document the model chose (link/download) with the session's cookies: CSV/TXT/HTML/JSON/ZIP/PDF → text."""
from __future__ import annotations

import base64
import csv
import io
import zipfile

from .observe import observe
from .session import SiteSession

MAX_DOC_CHARS = 40_000


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _csv(text: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=";,\t|")
    except csv.Error:
        return text
    rows = list(csv.reader(io.StringIO(text), dialect))
    return "\n".join("| " + " | ".join(c.strip() for c in r) + " |" for r in rows if any(c.strip() for c in r))


def _pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader  # optional dependency
    except ImportError:
        return "(PDF erkannt, aber kein PDF-Leser installiert — screenshot() des Viewers verwenden)"
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(f"--- Seite {i} ---\n{p.extract_text() or ''}" for i, p in enumerate(reader.pages, 1))


def _zip_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    if not info.flag_bits & 0x800:  # not flagged UTF-8: zipfile used cp437; many German tools wrote UTF-8 anyway
        try:
            name = name.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name


async def to_text(s: SiteSession, name: str, ctype: str, data: bytes, depth: int = 0) -> str:
    low, ctype = name.lower(), (ctype or "").lower()
    if low.endswith(".zip") or "zip" in ctype or data[:4] == b"PK\x03\x04":
        if depth > 1:
            return "(verschachteltes ZIP übersprungen)"
        out = []
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                inner = _zip_name(info)
                out.append(f"===== {inner} =====\n" + await to_text(s, inner, "", z.read(info), depth + 1))
        return "\n".join(out)
    if low.endswith(".pdf") or "pdf" in ctype or data[:5] == b"%PDF-":
        return _pdf(data)
    text = _decode(data)
    if low.endswith((".html", ".htm")) or "html" in ctype or text.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        page = await s.context.new_page()
        try:
            await page.route("**/*", lambda r: r.abort())  # render offline, no extra requests
            await page.set_content(text, wait_until="domcontentloaded")
            return (await observe(page)).text
        finally:
            await page.close()
    if low.endswith(".csv") or "csv" in ctype:
        return _csv(text)
    return text


FETCH_JS = """async (url) => {
  const r = await fetch(url, {credentials: "include"});
  const buf = new Uint8Array(await r.arrayBuffer());
  let bin = ""; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return {status: r.status, ctype: r.headers.get("content-type") || "", disp: r.headers.get("content-disposition") || "", b64: btoa(bin)};
}"""


async def _get(s: SiteSession, url: str) -> tuple[int, str, str, bytes]:
    try:
        resp = await s.context.request.get(url, timeout=30_000)
        return resp.status, resp.headers.get("content-type", ""), resp.headers.get("content-disposition", ""), await resp.body()
    except Exception:  # fall back to a fetch inside the page (same cookies, same origin)
        r = await s.page.evaluate(FETCH_JS, url)
        return r["status"], r["ctype"], r["disp"], base64.b64decode(r["b64"])


async def read_document(s: SiteSession, url: str) -> str:
    status, ctype, disp, data = await _get(s, url)
    if status >= 400:
        return f"Fehler: HTTP {status} beim Laden von {url}"
    name = disp.split("filename=")[-1].strip('"; ') if "filename=" in disp else url.split("?")[0].rsplit("/", 1)[-1]
    text = await to_text(s, name, ctype, data)
    head = f"Dokument: {name} ({ctype.split(';')[0] or 'unbekannt'}, {len(data)} Bytes)\n"
    if len(text) > MAX_DOC_CHARS:
        text = text[:MAX_DOC_CHARS] + f"\n[… {len(text) - MAX_DOC_CHARS} Zeichen gekürzt]"
    return head + text
