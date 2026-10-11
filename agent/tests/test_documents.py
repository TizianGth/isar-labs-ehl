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


def test_office_documents_are_read_as_text_not_listed_as_zip_archives():
    """#5: a bill of quantities or specification as .xlsx/.docx/.ods is a ZIP of XML; the model gets readable rows."""
    sheet_ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel_ns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    xlsx = _zip({
        "xl/workbook.xml": f'<workbook {sheet_ns} {rel_ns}><sheets><sheet name="LV" sheetId="1" r:id="rId1"/></sheets></workbook>'.encode(),
        "xl/_rels/workbook.xml.rels": b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                      b'<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/sharedStrings.xml": f'<sst {sheet_ns}><si><t>Pos</t></si><si><t>Menge</t></si><si><t>Zylinder X</t></si></sst>'.encode(),
        "xl/worksheets/sheet1.xml": f'<worksheet {sheet_ns}><sheetData>'
                                    f'<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                                    f'<row r="2"><c r="A2" t="inlineStr"><is><t>1.1</t></is></c><c r="B2"><v>1250</v></c>'
                                    f'<c r="D2" t="s"><v>2</v></c></row></sheetData></worksheet>'.encode()})
    text = parse(xlsx, "lv.xlsx", "", "u").text
    assert "=== sheet LV ===" in text and "Pos\tMenge" in text and "1.1\t1250\t\tZylinder X" in text
    word_ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    docx = _zip({"word/document.xml": f'<w:document {word_ns}><w:body><w:p><w:r><w:t>Lastenheft Rev. B</w:t></w:r></w:p>'
                                      f'<w:tbl><w:tr><w:tc><w:p><w:r><w:t>A-9</w:t></w:r></w:p></w:tc>'
                                      f'<w:tc><w:p><w:r><w:t>Hub 400 mm</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
                                      f'</w:body></w:document>'.encode()})
    text = parse(docx, "spec.docx", "", "u").text
    assert "Lastenheft Rev. B" in text and "A-9\tHub 400 mm" in text
    ods = _zip({"mimetype": b"application/vnd.oasis.opendocument.spreadsheet",
                "content.xml": b'<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                               b'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
                               b'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body>'
                               b'<table:table table:name="Preise"><table:table-row><table:table-cell><text:p>ab 100</text:p>'
                               b'</table:table-cell><table:table-cell><text:p>12,50</text:p></table:table-cell>'
                               b'</table:table-row></table:table></office:body></office:document-content>'})
    text = parse(ods, "preise.ods", "", "u").text
    assert "=== sheet Preise ===" in text and "ab 100\t12,50" in text
    plain = _zip({"a.txt": b"x", "b.csv": b"y"})                      # an ordinary ZIP is still listed
    assert parse(plain, "x.zip", "", "u").members and not parse(plain, "x.zip", "", "u").text
