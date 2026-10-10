#!/usr/bin/env python3
"""Dev-only pipeline: local package sanity checks and manual run recording.

Pipeline itself never uploads or runs models; check executes the package's tests.
"""
from __future__ import annotations
import argparse, decimal, json, os, re, shlex, subprocess, sys, uuid
from datetime import datetime, timezone
from pathlib import Path

BLOCKED_PREFIXES = ("dev/", "docs/", "smoke/", ".agents/", ".claude/", ".codex/", ".entire/")
BLOCKED_NAMES = ("evidence.json",)
BLOCKED_DIRS = ("fixtures", "oracle")
LEAK_EXTS = (".py", ".md", ".txt", ".json", ".toml", ".js", ".yaml", ".yml")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_package(arg: str, root: Path) -> Path:
    p = Path(arg)
    if not p.is_absolute():
        p = (root / p).resolve()
    else:
        p = p.resolve()
    if not p.exists() or not p.is_dir():
        raise ValueError(f"package does not exist: {arg}")
    try:
        p.relative_to(root)
    except ValueError:
        raise ValueError(f"package must be inside repo: {arg}")
    return p


def _run_atira(cmd: str, package: Path, timeout: int) -> subprocess.CompletedProcess:
    try:
        argv = shlex.split(cmd)
    except Exception as e:
        raise ValueError(f"malformed command: {e}")
    if not argv:
        raise ValueError("empty command")
    argv += ["test-run", str(package), "--dry-run", "--json"]
    return subprocess.run(argv, capture_output=True, text=True, shell=False, timeout=timeout)


def _validate_paths(paths: list, root: Path) -> list:
    seen = set()
    for rel in paths:
        if not isinstance(rel, str):
            raise ValueError(f"invalid path format: {repr(rel)}")
        if not rel or rel.startswith("/") or "\\" in rel:
            raise ValueError(f"invalid path format: {rel}")
        parts = rel.split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise ValueError(f"invalid path segments: {rel}")
        if rel in seen:
            raise ValueError(f"duplicate: {rel}")
        seen.add(rel)
        current = root
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"symlink in path component: {rel}")
            if not current.exists():
                break
        full = (root / rel).resolve()
        try:
            full.relative_to(root)
        except ValueError:
            raise ValueError(f"escapes repo: {rel}")
        if not full.exists():
            raise ValueError(f"missing file: {rel}")
        if not full.is_file():
            raise ValueError(f"not a file: {rel}")
    return sorted(seen)


def _is_blocked(rel: str) -> bool:
    parts = rel.split("/")
    if rel.startswith(BLOCKED_PREFIXES):
        return True
    name = parts[-1]
    if name in BLOCKED_NAMES:
        return True
    if name == ".env" or name.startswith(".env."):
        return True
    if name.endswith(".log") or name.endswith(".tar.gz"):
        return True
    # Block if ANY component is a blocked dir name
    if any(part in BLOCKED_DIRS for part in parts):
        return True
    return False


_KEY_RE = re.compile(r"(?<![A-Za-z])(VP|TZ|K|RP|PB|MET|FX|ER|GR|R|LH-VG)-\d{3,4}(?![A-Za-z0-9])")
_USER_RE = re.compile(r"(?<![A-Za-z0-9])u_[0-9a-f]{16}(?![A-Za-z0-9])")
_DOMAIN_RE = re.compile(r"kyora\.run")


def _load_needles(root: Path) -> dict:
    ref = root / "dev" / "oracle" / "public-1.json"
    if not ref.exists():
        raise FileNotFoundError(f"oracle missing: {ref}")
    try:
        data = json.loads(ref.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"oracle malformed: {e}")
    if not isinstance(data, dict):
        raise ValueError("oracle not an object")

    needles = {
        "tender-reference": set(),
        "material-number": set(),
        "public-key": set(),
    }

    tr = data.get("tender_reference")
    if not isinstance(tr, str) or not tr.strip():
        raise ValueError("oracle tender_reference invalid")
    needles["tender-reference"].add(tr.strip())

    positions = data.get("positions")
    if positions is None:
        raise ValueError("oracle missing positions")
    if not isinstance(positions, list):
        raise ValueError("oracle positions not list")
    for pos in positions:
        if not isinstance(pos, dict):
            raise ValueError("oracle position not object")
        mn = pos.get("material_number")
        if not isinstance(mn, str) or not mn.strip():
            raise ValueError("oracle material_number invalid")
        needles["material-number"].add(mn.strip())

    doc = root / "docs" / "03-site-recon.md"
    if not doc.exists():
        raise FileNotFoundError(f"missing docs/03-site-recon.md")
    for match in _KEY_RE.finditer(doc.read_text(encoding="utf-8", errors="replace")):
        needles["public-key"].add(match.group(0))

    return needles


def _scan_leaks(rel: str, text: str, needles: dict) -> list:
    hits = []
    seen = set()

    def _add(category):
        if category not in seen:
            seen.add(category)
            hits.append(f"{rel}:{category}")

    for category, needle_set in needles.items():
        for n in needle_set:
            if n and n in text:
                _add(category)

    if _USER_RE.search(text):
        _add("user-id")

    if _DOMAIN_RE.search(text):
        _add("domain")

    return hits


def _is_leak_target(rel: str) -> bool:
    p = Path(rel)
    return p.suffix.lower() in LEAK_EXTS and p.name != "uv.lock"


def cmd_check(args) -> int:
    root = repo_root()
    try:
        package = _resolve_package(args.package, root)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    atira_cmd = args.atira
    if not atira_cmd or not atira_cmd.strip():
        print("ERROR: --atira cannot be empty", file=sys.stderr)
        return 1

    try:
        cp = _run_atira(atira_cmd, package, 30)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("ERROR: atira test-run timed out", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"ERROR: atira CLI not found", file=sys.stderr)
        return 1
    except OSError:
        print("ERROR: failed to invoke atira CLI", file=sys.stderr)
        return 1

    if cp.returncode != 0:
        print("ERROR: atira exited non-zero", file=sys.stderr)
        return 1

    try:
        payload = json.loads(cp.stdout)
    except Exception:
        print("ERROR: atira returned invalid JSON", file=sys.stderr)
        return 1
    if not isinstance(payload, dict):
        print("ERROR: manifest not object", file=sys.stderr)
        return 1

    paths = payload.get("paths")
    files_count = payload.get("files")
    bytes_count = payload.get("bytes")
    if not isinstance(paths, list):
        print("ERROR: manifest missing 'paths' list", file=sys.stderr)
        return 1
    if not all(isinstance(p, str) for p in paths):
        print("ERROR: paths must be strings", file=sys.stderr)
        return 1
    if not isinstance(files_count, int) or isinstance(files_count, bool) or files_count != len(paths):
        print("ERROR: files count mismatch", file=sys.stderr)
        return 1
    if not isinstance(bytes_count, int) or isinstance(bytes_count, bool) or bytes_count < 0:
        print("ERROR: bytes invalid", file=sys.stderr)
        return 1
    if "agent.json" not in paths:
        print("ERROR: agent.json missing from payload paths", file=sys.stderr)
        return 1

    try:
        rels = _validate_paths(paths, package)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    blocked = [r for r in rels if _is_blocked(r)]
    if blocked:
        print("BLOCKED paths (isolate runtime in agent/ or exclude):", file=sys.stderr)
        for b in blocked:
            print(f"  - {b}", file=sys.stderr)
        return 1

    try:
        needles = _load_needles(root)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    leak_hits = []
    for rel in rels:
        p = package / rel
        if _is_leak_target(rel):
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                print(f"ERROR: read failed for {rel}", file=sys.stderr)
                return 1
            leak_hits.extend(_scan_leaks(rel, text, needles))

    if leak_hits:
        print("LEAK HITS (filename + category only):", file=sys.stderr)
        for h in leak_hits:
            print(f"  - {h}", file=sys.stderr)
        return 1

    if not args.skip_tests:
        tdir = package / "tests"
        if not tdir.is_dir():
            print("ERROR: tests dir missing (use --skip-tests)", file=sys.stderr)
            return 1
        if not any(f.name.startswith("test") and f.name.endswith(".py") for f in tdir.iterdir()):
            print("ERROR: no test*.py found (use --skip-tests)", file=sys.stderr)
            return 1
        try:
            tp = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                cwd=package, shell=False, timeout=60,
            )
        except subprocess.TimeoutExpired:
            print("ERROR: tests timed out", file=sys.stderr)
            return 1
        if tp.returncode != 0:
            return tp.returncode
    else:
        print("tests skipped")

    try:
        head, branch, dirty = _git_info(root)
    except Exception as e:
        print("ERROR: git info failed", file=sys.stderr)
        return 1

    print(f"OK: {len(rels)} files, {bytes_count} compressed payload bytes, commit={head}, dirty={dirty}")
    return 0


def _git_info(root: Path):
    def _run(a):
        r = subprocess.run(["git"] + a, cwd=root, capture_output=True, text=True, shell=False, timeout=10)
        if r.returncode != 0:
            raise Exception(f"git {a[0]} failed: {r.stderr.strip()}")
        return r.stdout.strip()
    head = _run(["rev-parse", "HEAD"])
    branch = _run(["rev-parse", "--abbrev-ref", "HEAD"])
    dirty = bool(_run(["status", "--porcelain"]))
    return head, branch, dirty


def _safe_decimal(label, s, minimum=0, maximum=None):
    if s is None:
        return None
    try:
        d = decimal.Decimal(s)
    except Exception:
        raise ValueError(f"{label}: not a decimal")
    if d.is_nan() or d.is_infinite():
        raise ValueError(f"{label}: NaN/Infinity not allowed")
    if d < minimum:
        raise ValueError(f"{label}: below minimum")
    if maximum is not None and d > maximum:
        raise ValueError(f"{label}: above maximum")
    return str(d)


def _validate_run_id(s: str) -> str:
    if not re.match(r"^[A-Za-z0-9._-]{1,64}$", s):
        raise ValueError("run-id must match [A-Za-z0-9._-]{1,64}")
    return s


def cmd_record(args) -> int:
    root = repo_root()
    try:
        rid = _validate_run_id(args.run_id)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    pkg_rel = None
    if args.package:
        try:
            pkg = _resolve_package(args.package, root)
            pkg_rel = str(pkg.relative_to(root))
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

    status = args.status
    if not status or not status.strip():
        print("ERROR: status invalid (empty)", file=sys.stderr)
        return 1
    if len(status) > 80 or "\n" in status or "\r" in status:
        print("ERROR: status invalid (too long or newline)", file=sys.stderr)
        return 1

    if args.note is not None and len(args.note) > 500:
        print("ERROR: note exceeds 500 chars", file=sys.stderr)
        return 1
    if args.eligible not in ("yes", "no", "unknown", None):
        print("ERROR: eligible must be yes|no|unknown", file=sys.stderr)
        return 1

    try:
        q = _safe_decimal("quality", args.quality, 0, 100)
        s = _safe_decimal("seconds", args.seconds, 0)
        c = _safe_decimal("cost-usd", args.cost_usd, 0)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    try:
        head, branch, dirty = _git_info(root)
    except Exception:
        print("ERROR: git info failed", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    runs = root / "dev" / "runs"
    fname = f"{now}-{uuid.uuid4().hex}.json"
    fpath = runs / fname

    rec = {
        "schema": "dev-record-1",
        "manual_source": "dev/pipeline.py record",
        "run_id": rid,
        "status": status,
        "quality": q,
        "seconds": s,
        "cost_usd": c,
        "eligible": args.eligible or "unknown",
        "note": args.note or None,
        "package": pkg_rel,
        "git": {"head": head, "branch": branch, "dirty": dirty, "checkout_at_record_time": True},
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        runs.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(fpath), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(rec, f, indent=2)
    except Exception as e:
        print(f"ERROR: write failed: {e}", file=sys.stderr)
        return 1

    print(f"recorded: {fpath}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dev/pipeline.py")
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check")
    c.add_argument("--package", required=True)
    c.add_argument("--atira", required=True)
    c.add_argument("--skip-tests", action="store_true")
    c.set_defaults(func=cmd_check)
    r = sub.add_parser("record")
    r.add_argument("--run-id", required=True)
    r.add_argument("--status", required=True)
    r.add_argument("--quality", default=None)
    r.add_argument("--eligible", choices=["yes", "no", "unknown"], default=None)
    r.add_argument("--seconds", default=None)
    r.add_argument("--cost-usd", default=None)
    r.add_argument("--note", default=None)
    r.add_argument("--package", default=None)
    r.set_defaults(func=cmd_record)
    args = ap.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
