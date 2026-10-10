"""Fail if public-scenario values appear in the submitted package (agent/). Dev only.

Values come from dev/oracle/public-1.json plus a list of keys/identifiers seen on the public sites.
usage: python dev/check_no_leaks.py [package_dir=agent]
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
pkg = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "agent")
ora = json.loads((ROOT / "dev/oracle/public-1.json").read_text())
needles = {ora["tender_reference"]}
for p in ora["positions"]:
    needles.add(p["material_number"])
    for f in ("unit_price", "base_unit_price", "total_net"):
        if f in p:
            needles.add(f"{p[f]:.2f}")
needles.add(f"{ora['total_net']:.2f}")
# identifiers seen on public-1 (keys, revisions, groups, logins, company data from the brief are allowed at runtime only)
needles |= set(re.findall(r"\b(?:VP|TZ|K|RP|PB|MET|FX|ER|GR|R|LH-VG)-\d{3,4}\b",
                          (ROOT / "docs/03-site-recon.md").read_text() + (ROOT / "dev/oracle/compute_public1.py").read_text()))
needles |= {"u_9c3cb06a5d21b197", "kyora.run", "Linden Hydraulik", "DE811234567", "Buchenstadt", "linden-hydraulik"}
hits = []
for f in pkg.rglob("*"):
    if f.is_file() and f.suffix in {".py", ".md", ".json", ".toml", ".js", ".txt", ".yaml", ".yml"} and f.name != "uv.lock":
        text = f.read_text(errors="ignore")
        hits += [f"{f.relative_to(ROOT)}: {n}" for n in needles if n in text]
if hits:
    print("LEAKS FOUND (public-scenario values in the package):"); print("\n".join(sorted(hits))); sys.exit(1)
print(f"ok: no public-scenario values in {pkg.relative_to(ROOT)}/ ({len(needles)} needles)")
