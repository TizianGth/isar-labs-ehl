"""B06: files the model selected (synthetic content only)."""
import io
import zipfile

from rfq.browser.documents import parse


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _pdf(text: str) -> bytes:
    """A minimal one-page PDF with a text layer."""
    stream = f"BT /F1 18 Tf 50 700 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


def test_zip_is_listed_first_and_only_the_chosen_member_is_read():
    data = _zip({"liste.csv": "Pos;Menge\n1;7\n".encode(), "info/notiz.txt": b"Hallo", "../evil.txt": b"x"})
    listing = parse(data, "akte.zip", "application/zip", "u")
    names = [name for name, _ in listing.members]
    assert "liste.csv" in names and "notiz.txt" in names and "evil.txt" in names     # never a path
    member = parse(data, "akte.zip", "application/zip", "u", member="liste.csv")
    assert member.text.startswith("Pos;Menge") and member.source == "u!liste.csv"
    assert "no file" in parse(data, "akte.zip", "", "u", member="fehlt.pdf").error


def test_pdf_text_layer_is_read():
    doc = parse(_pdf("Menge 42 Stueck"), "lv.pdf", "application/pdf", "u")
    assert "Menge 42" in doc.text


def test_login_page_instead_of_file_is_reported():
    doc = parse(b"<html><body><form><input type='password' name='p'></form></body></html>", "x", "text/html", "u")
    assert "login page" in doc.error


def test_csv_and_html_documents():
    assert parse("a;b\n1;2".encode("cp1252"), "x.csv", "text/csv", "u").text == "a;b\n1;2"
    html = b"<html><body><p>Menge <b>5</b></p><span aria-hidden=\"true\">99</span></body></html>"
    assert parse(html, "x.html", "text/html", "u").text.strip() == "Menge 5"
