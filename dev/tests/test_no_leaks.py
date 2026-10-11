"""Known public literals are caught without banning synthetic names or libraries."""
import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "check_no_leaks.py"
_spec = importlib.util.spec_from_file_location("check_no_leaks", _path)
checker = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = checker
_spec.loader.exec_module(checker)


def test_comments_and_escaped_public_paths_are_scanned(tmp_path):
    source = tmp_path / "module.py"
    source.write_text('# the current public header is /vk13?view=scales\n'
                      'path = "\\u002fmm03"\n'
                      'path2 = "\\/mmbe"\n'
                      'path3 = "%2Fdocuments%2Fspecification%2Fviewer"\n')
    paths = {"/vk13", "/mm03", "/mmbe", "/documents/specification/viewer"}
    hits = checker.scan_package(tmp_path, values=set(), paths=paths)
    assert {(hit.value, hit.line) for hit in hits} == {
        ("/vk13", 1), ("/mm03", 2), ("/mmbe", 3), ("/documents/specification/viewer", 4)}


def test_public_paths_discovered_from_fixtures_nested_frames_and_recon(tmp_path):
    fixtures = tmp_path / "dev/fixtures/public-1"
    fixtures.mkdir(parents=True)
    (fixtures / "page.html").write_text('<a href="/vk13?material=HZ-1609">price</a>'
                                      '<iframe srcdoc="&lt;a href=\'/documents/specific-record\'&gt;doc&lt;/a&gt;"></iframe>'
                                      '<script src="/static/vendor.js"></script>'
                                      '<form action="/login"></form>')
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "03-site-recon.md").write_text('The binding URL is `/distinct-record?view=header`.\n')
    assert checker.public_paths(tmp_path) == {"/vk13", "/documents/specific-record", "/distinct-record"}


def test_synthetic_identifiers_generic_controls_and_libraries_do_not_trigger(tmp_path):
    (tmp_path / "unit.py").write_text('reference = "VG-1"\nmaterial = "HZ-1"\n'
                                     'route = "/tools"\nroute2 = "/mm0300"\n'
                                     'ratio = 113.25\nselector = "#vk13_button"\n')
    (tmp_path / "library.js").write_text('const x = "/v1/chat/completions";\n')
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv/library.py").write_text('public = "VG-2027-7093"\n')
    hits = checker.scan_package(tmp_path, values={"VG-2027-7093", "HZ-1609", "13.25"}, paths={"/vk13", "/mm03"})
    assert hits == []


def test_fixture_identifiers_and_price_values_are_real_known_values():
    values = checker.public_values()
    assert {"VG-2027-7093", "HZ-1609", "2743.58"} <= values
    assert "VG-1" not in values and "HZ-1" not in values
    assert {"/vk13", "/mm03", "/mmbe"} <= checker.public_paths()


def test_package_outside_repo_and_js_literals_are_supported(tmp_path):
    source = tmp_path / "observe.js"
    source.write_text('const route = "https://synthetic.test/vk13?view=header";\n'
                      '// public ref VG-2027-7093 must stay outside the agent\n')
    hits = checker.scan_package(tmp_path, values={"VG-2027-7093"}, paths={"/vk13"})
    assert {(hit.kind, hit.line) for hit in hits} == {("pathname", 1), ("value", 2)}
