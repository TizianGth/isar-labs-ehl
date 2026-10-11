"""Scan all submitted source, including comments, for public-scenario leaks.

Known values come from development oracles/recon/fixtures. Distinctive public
paths are discovered from their actual links, rather than library names.
Usage: python3 dev/check_no_leaks.py [package_dir=agent]
"""
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SUFFIXES = {".py", ".md", ".json", ".toml", ".js", ".txt", ".yaml", ".yml",
                   ".html", ".css", ".sh", ".ini", ".cfg"}
EXCLUDED_DIRS = {".venv", "__pycache__", ".git", ".pytest_cache", "node_modules"}
# Authentication/search/container routes are generic. Object-specific descendants
# and distinctive transaction paths still carry scenario-specific knowledge.
GENERIC_PATHS = {"/", "/login", "/logout", "/signin", "/signout", "/search", "/tools",
                 "/documents", "/messages", "/api", "/static", "/assets", "/notice", "/command"}
IDENTIFIER = re.compile(
    r"\b(?:VG-\d{4}-\d{4}|LH-VG-\d{4}-\d{4}|HZ-\d{4}|"
    r"(?:VP|TZ|K|RP|PB|MET|FX|ER|GR|R)-\d{3,4}|PRD-\d{6})\b")


@dataclass(frozen=True)
class Hit:
    file: Path
    line: int
    value: str
    kind: str


def normalize_source(text: str) -> str:
    """Decode common literal escapes without evaluating executable source."""
    def unicode_escape(match):
        return chr(int(match.group(1) or match.group(2), 16))
    text = re.sub(r"\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})", unicode_escape, text)
    return unquote(unescape(text.replace(r"\/", "/")))


def route_path(value: str) -> str | None:
    value = unescape(value).strip()
    if not value.startswith(("/", "http://", "https://")):
        return None
    path = unquote(urlsplit(value).path).rstrip("/") or "/"
    if path in GENERIC_PATHS or path.startswith(("/assets/", "/static/")):
        return None
    return path


class FixtureLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attributes):
        for name, value in attributes:
            if name in {"href", "src", "action", "data-url", "data-href"} and value:
                path = route_path(value)
                if path:
                    self.paths.add(path)
            elif name == "srcdoc" and value:
                nested = FixtureLinks()
                nested.feed(unescape(value))
                self.paths.update(nested.paths)


def public_paths(root: Path = ROOT) -> set[str]:
    paths = set()
    for fixture in sorted((root / "dev/fixtures").glob("public-*/*.html")):
        parser = FixtureLinks()
        parser.feed(fixture.read_text(errors="ignore"))
        paths.update(parser.paths)
    recon = root / "docs/03-site-recon.md"
    if recon.exists():
        for code in re.findall(r"`([^`\n]+)`", recon.read_text()):
            # Preserve code-span navigation examples; ignore arbitrary source paths.
            for candidate in re.findall(r"(?<![\w/])/(?:[\w.-]+/?)+(?:\?[^\s`<>]*)?", code):
                path = route_path(candidate)
                if path and "<" not in path and path != "/path":
                    paths.add(path)
    return paths


def public_values(root: Path = ROOT) -> set[str]:
    oracle = json.loads((root / "dev/oracle/public-1.json").read_text())
    values = {oracle["tender_reference"]}
    for position in oracle["positions"]:
        values.add(position["material_number"])
        for key in ("unit_price", "base_unit_price", "total_net"):
            if key in position:
                values.add(f"{position[key]:.2f}")
    values.add(f"{oracle['total_net']:.2f}")
    sources = [root / "docs/03-site-recon.md", root / "dev/oracle/compute_public1.py"]
    sources.extend((root / "dev/fixtures").glob("public-*/*.html"))
    for source in sources:
        if source.exists():
            values.update(IDENTIFIER.findall(normalize_source(source.read_text(errors="ignore"))))
    values.update({"238,098", "2.743,58", "30,851", "13.25", "517.25", "1.0873", "64431", "PRD-635338",
                   "u_9c3cb06a5d21b197", "kyora.run", "Linden Hydraulik", "DE811234567", "Buchenstadt", "linden-hydraulik"})
    return values


def scan_package(package: Path, *, values: set[str], paths: set[str]) -> list[Hit]:
    hits = []
    patterns = [(value, "value", re.compile(r"(?<![\d])" + re.escape(value) + r"(?![\d])")
                 if re.fullmatch(r"[\d.,]+", value) else re.compile(re.escape(value))) for value in sorted(values)]
    patterns.extend((path, "pathname", re.compile(r"(?<![\w/])(?:https?://[^/\s'\"`]+)?" + re.escape(path) + r"(?=$|[/?#\s'\"`<>\]\)};:,])"))
                    for path in sorted(paths))
    for source in sorted(package.rglob("*")):
        relative = source.relative_to(package)
        if any(part in EXCLUDED_DIRS for part in relative.parts) or not source.is_file():
            continue
        if source.suffix not in SOURCE_SUFFIXES or source.name == "uv.lock":
            continue
        text = normalize_source(source.read_text(errors="ignore"))
        for value, kind, pattern in patterns:
            match = pattern.search(text)
            if match:
                hits.append(Hit(source, text.count("\n", 0, match.start()) + 1, value, kind))
    return hits


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    package = Path(args[0]).resolve() if args else ROOT / "agent"
    if not package.is_dir():
        print(f"error: package directory does not exist: {package}", file=sys.stderr)
        return 2
    values, paths = public_values(), public_paths()
    hits = scan_package(package, values=values, paths=paths)
    if hits:
        print("LEAKS FOUND (public-scenario values or distinctive paths in the package):")
        for hit in hits:
            print(f"{hit.file.relative_to(package)}:{hit.line}: {hit.kind}: {hit.value}")
        return 1
    print(f"ok: no public-scenario values or distinctive paths in {package}/ ({len(values)} values, {len(paths)} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
