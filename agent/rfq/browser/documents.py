"""Files the model chose on a website: download links/buttons, ZIP archives, PDF, CSV/TXT, HTML documents.

Only what the model selected is read: a ZIP is first listed, then the member the model asks for is read. Session
cookies of the site are used; size limits keep the run safe; a login page instead of a file is reported as such.
"""
import asyncio
import io
import re
import zipfile
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

from playwright.async_api import Error as PlaywrightError

MAX_BYTES = 15 * 1024 * 1024
MAX_MEMBER_BYTES = 5 * 1024 * 1024
MAX_MEMBERS = 300


@dataclass
class Document:
    source: str                 # URL (and !member) the content came from
    name: str = ""
    mime: str = ""
    text: str = ""
    members: list | None = None  # for archives without member: [(name, size)]
    error: str = ""

    def describe(self) -> str:
        if self.members is not None:
            listing = "\n".join(f"  - {name} ({size} bytes)" for name, size in self.members[:80])
            return f"archive {self.name or self.source} with {len(self.members)} files:\n{listing}\n" \
                   f"call read_document again with member=<file name> to read one"
        return f"document {self.name or self.source} ({self.mime or 'unknown type'}, {len(self.text)} characters of text)"


async def read_document(site, ref: str | None = None, url: str | None = None, member: str | None = None) -> Document:
    """Fetch the file behind a link/button (ref) or an observed address (url) and turn it into text."""
    try:
        data, name, mime, source = await _fetch(site, ref, url)
    except _Failure as failure:
        return Document(source=url or ref or "", error=str(failure))
    return parse(data, name, mime, source, member)


class _Failure(Exception):
    pass


async def _fetch(site, ref, url) -> tuple[bytes, str, str, str]:
    if ref:
        _, element = await site._element(ref)
        href = await element.get_attribute("href")
        if href and not href.startswith(("#", "javascript:")):
            url = site.absolute(href)
        else:   # a button that starts a download
            try:
                async with site.page.expect_download(timeout=15000) as info:
                    await element.click(timeout=5000)
                download = await info.value
                path = await download.path()
                data = open(path, "rb").read() if path else b""
                return _limit(data), download.suggested_filename, _guess_mime(download.suggested_filename), download.url
            except PlaywrightError as error:
                raise _Failure(f"clicking it did not start a download ({str(error).splitlines()[0][:120]})")
    if not url:
        raise _Failure("give the ref of a download link/button or an address you saw")
    url = site.absolute(url)
    if urlparse(url).netloc != site.origin:
        raise _Failure("only files of this website can be read")
    if not site.was_seen(url):
        raise _Failure("this address was not offered on any page you saw")
    for attempt in range(3):
        try:
            response = await site.context.request.get(url, timeout=30000)
        except PlaywrightError as error:
            if attempt == 2:
                raise _Failure(f"download failed: {str(error).splitlines()[0][:120]}")
            await asyncio.sleep(1 + attempt)
            continue
        if response.status >= 500 and attempt < 2:
            await asyncio.sleep(1 + attempt)
            continue
        if response.status >= 400:
            raise _Failure(f"the website answered HTTP {response.status}")
        data = await response.body()
        disposition = response.headers.get("content-disposition", "")
        match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition)
        name = unquote(match.group(1)) if match else unquote(urlparse(url).path.rsplit("/", 1)[-1])
        return _limit(data), name, response.headers.get("content-type", "").split(";")[0].strip(), response.url
    raise _Failure("download failed")


def _limit(data: bytes) -> bytes:
    if len(data) > MAX_BYTES:
        raise _Failure(f"file too large ({len(data)} bytes)")
    return data


def _guess_mime(name: str) -> str:
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    return {"zip": "application/zip", "pdf": "application/pdf", "csv": "text/csv", "txt": "text/plain",
            "html": "text/html", "htm": "text/html", "json": "application/json", "xml": "text/xml"}.get(ext, "")


def parse(data: bytes, name: str, mime: str, source: str, member: str | None = None) -> Document:
    kind = _kind(data, name, mime)
    if kind == "zip":
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            return Document(source, name, mime, error="the file is not a readable ZIP archive")
        infos = [i for i in archive.infolist() if not i.is_dir()][:MAX_MEMBERS]
        if not member:
            return Document(source, name, mime, members=[(_member_name(i), i.file_size) for i in infos])
        wanted = next((i for i in infos if _member_name(i) == member or i.filename == member), None) or \
                 next((i for i in infos if member.lower() in _member_name(i).lower()), None)
        if wanted is None:
            return Document(source, name, mime, error=f"no file {member!r} in the archive; files: "
                            + ", ".join(_member_name(i) for i in infos[:40]))
        if wanted.file_size > MAX_MEMBER_BYTES:
            return Document(source, name, mime, error=f"{member} is too large ({wanted.file_size} bytes)")
        inner = archive.read(wanted)
        doc = parse(inner, _member_name(wanted), _guess_mime(_member_name(wanted)), f"{source}!{_member_name(wanted)}")
        return doc
    if kind == "pdf":
        return Document(source, name, mime or "application/pdf", text=_pdf_text(data))
    text = _decode(data)
    if kind == "html":
        if re.search(r'<input[^>]+type=["\']?password', text, re.I):
            return Document(source, name, mime, error="got a login page instead of the file (session expired?)")
        text = _html_text(text)
    return Document(source, name, mime or "text/plain", text=text)


def _member_name(info: zipfile.ZipInfo) -> str:
    """Archive names are often CP437-decoded UTF-8 (umlauts): repair, and never use them as paths."""
    raw = info.filename
    if not info.flag_bits & 0x800:
        try:
            raw = raw.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return raw.replace("\\", "/").split("/")[-1] or raw


def _kind(data: bytes, name: str, mime: str) -> str:
    if data[:4] == b"PK\x03\x04" or "zip" in mime:
        return "zip"
    if data[:5] == b"%PDF-" or "pdf" in mime:
        return "pdf"
    head = data[:600].lower()
    if "html" in mime or b"<html" in head or b"<!doctype html" in head:
        return "html"
    return "text"


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _html_text(markup: str) -> str:
    import html as html_lib
    markup = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", markup, flags=re.S | re.I)
    markup = re.sub(r'<[^>]*aria-hidden="true"[^>]*>.*?</[^>]+>', " ", markup, flags=re.S)
    markup = re.sub(r"<(br|/p|/div|/tr|/li|/h\d)[^>]*>", "\n", markup, flags=re.I)
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", markup))
    text = re.sub(r"[​-‍⁠﻿­]", "", text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "(PDF text extraction is not available; use inspect_visual on the viewer)"
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages[:60]]
    except Exception as error:   # malformed PDFs
        return f"(PDF could not be read: {type(error).__name__})"
    text = "\n\n".join(f"--- page {i + 1} ---\n{t}" for i, t in enumerate(pages))
    return text if text.strip() else "(the PDF has no text layer; it is probably scanned: use inspect_visual)"
