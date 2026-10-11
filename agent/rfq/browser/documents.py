"""Files the model chose on a website: download links/buttons, ZIP archives, PDF, CSV/TXT, HTML documents.

Only what the model selected is read: a ZIP is first listed, then the member the model asks for is read. Session
cookies of the site are used; size limits keep the run safe; a login page instead of a file is reported as such.
"""
import asyncio
import io
import re
import zipfile
import xml.etree.ElementTree as ET
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
            "html": "text/html", "htm": "text/html", "json": "application/json", "xml": "text/xml",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "ods": "application/vnd.oasis.opendocument.spreadsheet",
            "odt": "application/vnd.oasis.opendocument.text"}.get(ext, "")


def parse(data: bytes, name: str, mime: str, source: str, member: str | None = None) -> Document:
    kind = _kind(data, name, mime)
    if kind == "zip":
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            return Document(source, name, mime, error="the file is not a readable ZIP archive")
        office = _office_text(archive)            # .xlsx/.docx/.ods/.odt are ZIP archives of XML: read them as text
        if office is not None:
            return Document(source, name, mime or "application/office", text=office)
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


def _office_text(archive: zipfile.ZipFile) -> str | None:
    """Text of an office document (tables as tab-separated rows), or None if the archive is not one or unreadable."""
    names = set(archive.namelist())
    try:
        if "xl/workbook.xml" in names:
            return _xlsx_text(archive, names)
        if "word/document.xml" in names:
            return _docx_text(archive)
        if "content.xml" in names and "mimetype" in names:
            return _odf_text(archive, archive.read("mimetype").decode("ascii", "replace"))
    except Exception:          # malformed office file: fall back to listing the archive
        return None
    return None


def _xml(archive: zipfile.ZipFile, name: str):
    info = archive.getinfo(name)
    if info.file_size > MAX_MEMBER_BYTES:
        raise ValueError(f"{name} too large")
    return ET.fromstring(archive.read(name))


def _xlsx_text(archive: zipfile.ZipFile, names: set[str]) -> str:
    m = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rel = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    shared = ["".join(t.text or "" for t in si.iter(f"{m}t")) for si in _xml(archive, "xl/sharedStrings.xml").iter(f"{m}si")] \
        if "xl/sharedStrings.xml" in names else []
    targets = {}
    if "xl/_rels/workbook.xml.rels" in names:
        for r in _xml(archive, "xl/_rels/workbook.xml.rels"):
            targets[r.get("Id")] = "xl/" + r.get("Target", "").lstrip("/").removeprefix("xl/")
    out = []
    for number, sheet in enumerate(_xml(archive, "xl/workbook.xml").iter(f"{m}sheet"), 1):
        path = targets.get(sheet.get(f"{rel}id")) or f"xl/worksheets/sheet{number}.xml"
        if path not in names:
            continue
        out.append(f"=== sheet {sheet.get('name')} ===")
        for row in list(_xml(archive, path).iter(f"{m}row"))[:5000]:
            cells: dict[int, str] = {}
            for c in row.iter(f"{m}c"):
                kind, value = c.get("t"), c.find(f"{m}v")
                if kind == "s" and value is not None and value.text and value.text.isdigit():
                    text = shared[int(value.text)] if int(value.text) < len(shared) else ""
                elif kind == "inlineStr":
                    text = "".join(t.text or "" for t in c.iter(f"{m}t"))
                else:
                    text = value.text if value is not None and value.text else ""
                cells[_column(c.get("r")) if c.get("r") else len(cells)] = text.strip()
            if any(cells.values()):
                out.append("\t".join(cells.get(i, "") for i in range(max(cells) + 1)))
    return "\n".join(out)


def _column(reference: str) -> int:
    index = 0
    for char in re.match(r"[A-Z]+", reference.upper()).group(0):
        index = index * 26 + ord(char) - 64
    return index - 1


def _docx_text(archive: zipfile.ZipFile) -> str:
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    body = _xml(archive, "word/document.xml").find(f"{w}body")
    out = []
    for block in body if body is not None else []:
        if block.tag == f"{w}p":
            out.append("".join(t.text or "" for t in block.iter(f"{w}t")))
        elif block.tag == f"{w}tbl":
            for tr in block.iter(f"{w}tr"):
                out.append("\t".join("".join(t.text or "" for t in tc.iter(f"{w}t")).strip() for tc in tr.findall(f"{w}tc")))
    return "\n".join(line for line in out if line.strip())


def _odf_text(archive: zipfile.ZipFile, mimetype: str) -> str:
    table, text = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}", "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    root = _xml(archive, "content.xml")
    cell_text = lambda cell: " ".join("".join(p.itertext()).strip() for p in cell.iter(f"{text}p"))
    out = []
    if "spreadsheet" in mimetype:
        for sheet in root.iter(f"{table}table"):
            out.append(f"=== sheet {sheet.get(f'{table}name')} ===")
            for row in list(sheet.iter(f"{table}table-row"))[:5000]:
                cells = []
                for cell in row.findall(f"{table}table-cell"):
                    repeat = min(int(cell.get(f"{table}number-columns-repeated", "1")), 50)
                    cells += [cell_text(cell)] * repeat
                if any(cells):
                    out.append("\t".join(cells).rstrip("\t"))
    else:
        for element in root.iter():
            if element.tag in (f"{text}p", f"{text}h"):
                out.append("".join(element.itertext()))
    return "\n".join(line for line in out if line.strip())


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
