#!/usr/bin/env python3
"""Dev-only pipeline: local package sanity checks and manual run recording.

Pipeline itself never uploads or runs models; check executes the package's tests.
"""
from __future__ import annotations
import argparse, decimal, hashlib, json, os, re, shlex, subprocess, sys, tarfile, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path

BLOCKED_PREFIXES = ("dev/", "docs/", "smoke/", ".agents/", ".claude/", ".codex/", ".entire/")
BLOCKED_NAMES = ("evidence.json",)
BLOCKED_DIRS = ("fixtures", "oracle")
LEAK_EXTS = (".py", ".md", ".txt", ".json", ".toml", ".js", ".yaml", ".yml")

# Cache/build artifacts that must never appear in a package manifest.
BLOCKED_CACHE_DIRS = ("__pycache__", ".pytest_cache", ".venv", ".git", "node_modules")
BLOCKED_FILE_SUFFIXES = (".pyc", ".pyo")

# Cap on total uncompressed bytes read from an uploaded archive during verify-run.
MAX_ARCHIVE_READ = 20 * 1024 * 1024


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
    # Block cache/build artifacts in ANY path component and compiled bytecode files.
    if any(part in BLOCKED_CACHE_DIRS for part in parts):
        return True
    if name.endswith(BLOCKED_FILE_SUFFIXES):
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


def _snapshot_package(package: Path, rels: list) -> dict:
    """Capture {rel: (size, sha256)} for the included package files."""
    snap = {}
    for rel in rels:
        p = package / rel
        data = p.read_bytes()
        snap[rel] = (len(data), hashlib.sha256(data).hexdigest())
    return snap


def _write_private_json(path: Path, obj: dict) -> None:
    """Write JSON with mode 0600 using exclusive create (like record)."""
    root = repo_root()
    parent = path.parent.resolve()
    try:
        parent.relative_to(root.resolve())
    except ValueError:
        raise ValueError("destination parent must stay inside repo root")
    parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=2)


def _run_tests(package: Path, runner: str) -> int:
    """Run the package's tests. Returns 0 on success, nonzero on failure.

    pytest is the default; unittest is opt-in via --test-runner unittest.
    A package with no collected tests must fail (pytest exit 5, unittest zero
    discovered tests). Missing pytest gives an actionable error without fallback.
    """
    if runner == "pytest":
        try:
            probe = subprocess.run(
                [sys.executable, "-m", "pytest", "--version"],
                cwd=package, capture_output=True, text=True, shell=False, timeout=30,
            )
        except FileNotFoundError:
            print("ERROR: pytest not found; install dev deps with 'uv sync' in agent/", file=sys.stderr)
            return 1
        except subprocess.TimeoutExpired:
            print("ERROR: pytest probe timed out", file=sys.stderr)
            return 1
        except OSError:
            print("ERROR: failed to invoke pytest; install dev deps with 'uv sync' in agent/", file=sys.stderr)
            return 1
        if probe.returncode != 0:
            print("ERROR: pytest not available; install dev deps with 'uv sync' in agent/", file=sys.stderr)
            return 1
        try:
            tp = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "tests"],
                cwd=package, shell=False, timeout=60,
            )
        except subprocess.TimeoutExpired:
            print("ERROR: tests timed out", file=sys.stderr)
            return 1
        except OSError:
            print("ERROR: failed to invoke pytest", file=sys.stderr)
            return 1
        if tp.returncode == 5:
            print("ERROR: pytest collected no tests (exit 5)", file=sys.stderr)
            return 1
        return tp.returncode
    else:
        # Legacy unittest runner (opt-in). Reject zero discovered tests.
        try:
            code = (
                "import unittest, sys\n"
                "suite = unittest.TestLoader().discover('tests')\n"
                "if not suite.countTestCases():\n"
                "    print('ERROR: unittest discovered no tests', file=sys.stderr)\n"
                "    sys.exit(1)\n"
                "result = unittest.TextTestRunner().run(suite)\n"
                "sys.exit(0 if result.wasSuccessful() else 1)\n"
            )
            tp = subprocess.run(
                [sys.executable, "-c", code],
                cwd=package, shell=False, timeout=60,
            )
        except subprocess.TimeoutExpired:
            print("ERROR: tests timed out", file=sys.stderr)
            return 1
        except OSError:
            print("ERROR: failed to invoke unittest", file=sys.stderr)
            return 1
        return tp.returncode


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

    # Capture git and created_at BEFORE pre-test snapshot
    try:
        head, branch, dirty = _git_info(root)
    except Exception as e:
        print("ERROR: git info failed", file=sys.stderr)
        return 1
    captured_created_at = datetime.now(timezone.utc).isoformat()

    # Capture package content BEFORE testing so tests cannot mutate it unnoticed.
    try:
        before = _snapshot_package(package, rels)
    except Exception as e:
        print(f"ERROR: snapshot failed: {e}", file=sys.stderr)
        return 1

    skip_tests = getattr(args, "skip_tests", False)
    runner = getattr(args, "test_runner", "pytest")
    if not skip_tests:
        tdir = package / "tests"
        if not tdir.is_dir():
            print("ERROR: tests dir missing (use --skip-tests)", file=sys.stderr)
            return 1
        if not any(f.name.startswith("test") and f.name.endswith(".py") for f in tdir.iterdir()):
            print("ERROR: no test*.py found (use --skip-tests)", file=sys.stderr)
            return 1
        rc = _run_tests(package, runner)
        if rc != 0:
            return rc
    else:
        print("tests skipped")

    # Recheck git HEAD/branch after tests and fail if changed
    try:
        head2, branch2, dirty2 = _git_info(root)
    except Exception as e:
        print("ERROR: git info failed post-test", file=sys.stderr)
        return 1
    if head2 != head or branch2 != branch:
        print("ERROR: git HEAD/branch changed during tests", file=sys.stderr)
        return 1

    # Re-run the official dry-run and compare paths AND hashes so tests cannot
    # mutate the package unnoticed (ignored caches don't affect comparison).
    try:
        cp2 = _run_atira(atira_cmd, package, 30)
    except Exception as e:
        print(f"ERROR: post-test dry-run failed: {e}", file=sys.stderr)
        return 1
    if cp2.returncode != 0:
        print("ERROR: post-test atira exited non-zero", file=sys.stderr)
        return 1
    try:
        payload2 = json.loads(cp2.stdout)
    except Exception:
        print("ERROR: post-test atira returned invalid JSON", file=sys.stderr)
        return 1
    if not isinstance(payload2, dict):
        print("ERROR: post-test manifest not object", file=sys.stderr)
        return 1
    paths2 = payload2.get("paths")
    files_count2 = payload2.get("files")
    bytes_count2 = payload2.get("bytes")
    if not isinstance(paths2, list) or not all(isinstance(p, str) for p in paths2):
        print("ERROR: post-test manifest paths invalid", file=sys.stderr)
        return 1
    if not isinstance(files_count2, int) or isinstance(files_count2, bool) or files_count2 != len(paths2):
        print("ERROR: post-test files count mismatch", file=sys.stderr)
        return 1
    if not isinstance(bytes_count2, int) or isinstance(bytes_count2, bool) or bytes_count2 < 0:
        print("ERROR: post-test bytes invalid", file=sys.stderr)
        return 1

    try:
        rels2 = _validate_paths(paths2, package)
    except ValueError as e:
        print(f"ERROR: post-test paths invalid: {e}", file=sys.stderr)
        return 1
    if sorted(rels2) != sorted(rels):
        print("ERROR: package paths changed during tests", file=sys.stderr)
        return 1
    try:
        after = _snapshot_package(package, rels)
    except Exception as e:
        print(f"ERROR: post-test snapshot failed: {e}", file=sys.stderr)
        return 1
    if after != before:
        print("ERROR: package content changed during tests", file=sys.stderr)
        return 1

    # Write a private check receipt (content snapshot, not the uploaded tarball SHA).
    # Use captured git, captured timestamp and final manifest bytes.
    included = [{"path": rel, "size": before[rel][0], "sha256": before[rel][1]} for rel in rels]
    pkg_rel = str(package.relative_to(root))
    receipt = {
        "schema": "dev-preflight-1",
        "package": pkg_rel,
        "runner": "skipped" if skip_tests else runner,
        "skipped": bool(skip_tests),
        "included": included,
        "compressed_bytes": bytes_count2,
        "git": {"head": head, "branch": branch, "dirty": dirty},
        "created_at": captured_created_at,
    }
    runs = root / "dev" / "runs"
    fname = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex}.json"
    fpath = runs / fname
    try:
        _write_private_json(fpath, receipt)
    except Exception as e:
        print(f"ERROR: receipt write failed: {e}", file=sys.stderr)
        return 1

    print(f"OK: {len(rels)} files, {bytes_count2} compressed payload bytes, commit={head}, dirty={dirty}")
    print(f"receipt: {fpath}")
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


def _validate_receipt(receipt: dict) -> None:
    """Strictly validate a dev-preflight-1 receipt. Raises ValueError on problems."""
    if not isinstance(receipt, dict):
        raise ValueError("receipt not an object")
    if receipt.get("schema") != "dev-preflight-1":
        raise ValueError("receipt schema invalid")

    git = receipt.get("git")
    if not isinstance(git, dict):
        raise ValueError("receipt git invalid")
    head = git.get("head")
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("receipt git head invalid")
    branch = git.get("branch")
    if not isinstance(branch, str) or not branch:
        raise ValueError("receipt git branch invalid")
    dirty = git.get("dirty")
    if not isinstance(dirty, bool):
        raise ValueError("receipt git dirty invalid")

    pkg = receipt.get("package")
    if not isinstance(pkg, str):
        raise ValueError("receipt package invalid")
    if pkg != ".":
        if pkg.startswith("/") or "\\" in pkg:
            raise ValueError("receipt package path invalid")
        parts = pkg.split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise ValueError("receipt package path segments invalid")

    runner = receipt.get("runner")
    if runner not in ("pytest", "unittest", "skipped"):
        raise ValueError("receipt runner invalid")
    skipped = receipt.get("skipped")
    if not isinstance(skipped, bool):
        raise ValueError("receipt skipped invalid")
    if (runner == "skipped") != skipped:
        raise ValueError("receipt runner/skipped inconsistent")

    comp_bytes = receipt.get("compressed_bytes")
    if not isinstance(comp_bytes, int) or isinstance(comp_bytes, bool) or comp_bytes < 0:
        raise ValueError("receipt compressed_bytes invalid")

    created_at = receipt.get("created_at")
    if not isinstance(created_at, str):
        raise ValueError("receipt created_at invalid")
    try:
        dt = datetime.fromisoformat(created_at)
        if dt.tzinfo is None:
            raise ValueError("receipt created_at not timezone-aware")
    except Exception:
        raise ValueError("receipt created_at invalid ISO format")

    included = receipt.get("included")
    if not isinstance(included, list) or not included or len(included) > 2000:
        raise ValueError("receipt included invalid")
    seen = set()
    for entry in included:
        if not isinstance(entry, dict):
            raise ValueError("receipt entry not object")
        path = entry.get("path")
        if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path:
            raise ValueError(f"receipt entry path invalid: {repr(path)}")
        parts = path.split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise ValueError(f"receipt entry path segments invalid: {path}")
        if path in seen:
            raise ValueError(f"receipt duplicate path: {path}")
        seen.add(path)
        size = entry.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError(f"receipt entry size invalid: {repr(size)}")
        sha = entry.get("sha256")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError(f"receipt entry sha256 invalid: {repr(sha)}")


def _validate_receipt_path(receipt_path: Path, root: Path) -> Path:
    """Ensure the receipt file resides inside repo dev/runs (resolve/symlink safe)."""
    root_resolved = root.resolve()
    runs_dir = (root / "dev" / "runs").resolve()
    try:
        runs_dir.relative_to(root_resolved)
    except ValueError:
        raise ValueError("runs directory must reside inside repo root")

    resolved = receipt_path.resolve()
    try:
        resolved.relative_to(runs_dir)
    except ValueError:
        raise ValueError("receipt path must reside inside repo dev/runs")

    if not resolved.is_file():
        raise ValueError("receipt file missing")
    return resolved


def _read_archive_members(path: Path) -> dict:
    """Read tar members without extraction. Returns {name: (size, sha256)}.

    Rejects unsafe/duplicate paths, links/nonregular files. Caps total
    uncompressed read at MAX_ARCHIVE_READ. Directory entries are ignored.
    """
    members = {}
    seen = set()
    total_declared = 0
    count = 0
    with tarfile.open(str(path), "r|*") as tf:
        for m in tf:
            count += 1
            if count > 2000:
                raise ValueError("archive member count exceeds cap")

            name = m.name
            if m.isdir() and name.endswith("/"):
                name = name[:-1]
            if not name or name.startswith("/") or "\\" in name:
                raise ValueError(f"unsafe archive path: {name}")
            if any(part in ("", ".", "..") for part in name.split("/")):
                raise ValueError(f"unsafe archive path segments: {name}")
            if name in seen:
                raise ValueError(f"duplicate archive path: {name}")
            seen.add(name)

            if m.isdir():
                continue

            if not m.isfile():
                raise ValueError(f"non-regular archive member: {name}")

            # Check declared size against cap BEFORE reading
            if m.size < 0:
                raise ValueError(f"invalid size for {name}")
            if total_declared + m.size > MAX_ARCHIVE_READ:
                raise ValueError("archive uncompressed size exceeds cap")
            total_declared += m.size

            fobj = tf.extractfile(m)
            if fobj is None:
                raise ValueError(f"cannot read archive member: {name}")

            # Read bounded declared size + 1 to verify exact length
            data = fobj.read(m.size + 1)
            if len(data) != m.size:
                raise ValueError(f"archive member size mismatch: {name}")

            members[name] = (len(data), hashlib.sha256(data).hexdigest())

    return members


def cmd_verify_run(args) -> int:
    root = repo_root()
    try:
        rid = _validate_run_id(args.run_id)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    receipt_path = Path(args.receipt)
    if not receipt_path.is_absolute():
        receipt_path = (root / receipt_path)
    try:
        receipt_path = _validate_receipt_path(receipt_path, root)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ERROR: receipt unreadable: {e}", file=sys.stderr)
        return 1
    try:
        _validate_receipt(receipt)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    atira_cmd = args.atira
    if not atira_cmd or not atira_cmd.strip():
        print("ERROR: --atira cannot be empty", file=sys.stderr)
        return 1

    # Fetch the exact uploaded archive via the official CLI into a temp dir.
    with tempfile.TemporaryDirectory() as tmp:
        try:
            argv = shlex.split(atira_cmd)
        except Exception as e:
            print(f"ERROR: malformed command: {e}", file=sys.stderr)
            return 1
        if not argv:
            print("ERROR: empty command", file=sys.stderr)
            return 1
        argv += ["package", rid, "--out", tmp, "--json"]
        try:
            cp = subprocess.run(argv, capture_output=True, text=True, shell=False, timeout=60)
        except FileNotFoundError:
            print("ERROR: atira CLI not found", file=sys.stderr)
            return 1
        except subprocess.TimeoutExpired:
            print("ERROR: atira package fetch timed out", file=sys.stderr)
            return 1
        except OSError:
            print("ERROR: failed to invoke atira CLI", file=sys.stderr)
            return 1
        if cp.returncode != 0:
            print("ERROR: atira package fetch exited non-zero", file=sys.stderr)
            return 1
        try:
            payload = json.loads(cp.stdout)
        except Exception:
            print("ERROR: atira package returned invalid JSON", file=sys.stderr)
            return 1
        if not isinstance(payload, dict):
            print("ERROR: atira package payload not object", file=sys.stderr)
            return 1

        # Require payload.id == rid
        payload_id = payload.get("id")
        if payload_id != rid:
            print("ERROR: payload id mismatch", file=sys.stderr)
            return 1

        saved = payload.get("saved")
        if not isinstance(saved, dict):
            print("ERROR: atira package missing 'saved'", file=sys.stderr)
            return 1

        saved_path = saved.get("path")
        if not isinstance(saved_path, str) or not saved_path:
            print("ERROR: atira package saved path invalid", file=sys.stderr)
            return 1
        saved_path = Path(saved_path)
        try:
            saved_path.resolve().relative_to(Path(tmp).resolve())
        except ValueError:
            print("ERROR: saved path escapes temp dir", file=sys.stderr)
            return 1
        if not saved_path.is_file():
            print("ERROR: saved archive missing", file=sys.stderr)
            return 1

        saved_sha = saved.get("sha256")
        if not isinstance(saved_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", saved_sha):
            print("ERROR: atira package saved sha256 invalid", file=sys.stderr)
            return 1

        saved_bytes = saved.get("bytes")
        if not isinstance(saved_bytes, int) or isinstance(saved_bytes, bool) or saved_bytes < 0:
            print("ERROR: atira package saved bytes invalid", file=sys.stderr)
            return 1

        server_sha = payload.get("sha256")
        if not isinstance(server_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", server_sha):
            print("ERROR: atira package sha256 invalid", file=sys.stderr)
            return 1

        # Cap actual compressed stat.size <= MAX_ARCHIVE_READ before read
        try:
            stat_size = saved_path.stat().st_size
        except Exception as e:
            print(f"ERROR: cannot stat archive: {e}", file=sys.stderr)
            return 1
        if stat_size > MAX_ARCHIVE_READ:
            print("ERROR: archive compressed size exceeds cap", file=sys.stderr)
            return 1

        # Hash the actual compressed bytes and match the server listing sha256.
        try:
            comp = saved_path.read_bytes()
        except Exception as e:
            print(f"ERROR: cannot read archive: {e}", file=sys.stderr)
            return 1

        # saved.bytes == actual compressed size
        if saved_bytes != len(comp):
            print("ERROR: saved bytes mismatch actual size", file=sys.stderr)
            return 1

        # saved.sha256 == server_sha
        if saved_sha != server_sha:
            print("ERROR: saved sha256 mismatch server sha256", file=sys.stderr)
            return 1

        actual_sha = hashlib.sha256(comp).hexdigest()
        if actual_sha != server_sha:
            print("ERROR: archive checksum mismatch vs server listing", file=sys.stderr)
            return 1

        # Read tar members without extraction.
        try:
            members = _read_archive_members(saved_path)
        except Exception as e:
            print(f"ERROR: archive read failed: {e}", file=sys.stderr)
            return 1

        # Compare exact archive file names, byte sizes and sha256 to receipt entries.
        receipt_entries = {e["path"]: (e["size"], e["sha256"]) for e in receipt["included"]}
        mismatches = []
        for name in sorted(set(receipt_entries) | set(members)):
            if name not in members:
                mismatches.append((name, "missing-in-archive"))
            elif name not in receipt_entries:
                mismatches.append((name, "extra-in-archive"))
            else:
                rsize, rsha = receipt_entries[name]
                asize, asha = members[name]
                if rsize != asize:
                    mismatches.append((name, "size"))
                elif rsha != asha:
                    mismatches.append((name, "sha256"))
        if mismatches:
            print("VERIFY MISMATCH (name + category only):", file=sys.stderr)
            for name, cat in mismatches:
                print(f"  - {name}: {cat}", file=sys.stderr)
            return 1

        # Success: save a private dev-verified-package-1 receipt.
        verified = {
            "schema": "dev-verified-package-1",
            "run_id": rid,
            "archive_sha256": actual_sha,
            "receipt": receipt_path.name,
            "receipt_git": receipt["git"],
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
        runs = root / "dev" / "runs"
        fname = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex}.json"
        fpath = runs / fname
        try:
            _write_private_json(fpath, verified)
        except Exception as e:
            print(f"ERROR: verified receipt write failed: {e}", file=sys.stderr)
            return 1
        print(f"verified: FILE CONTENTS match snapshot; receipt: {fpath}")
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dev/pipeline.py")
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check")
    c.add_argument("--package", required=True)
    c.add_argument("--atira", required=True)
    c.add_argument("--skip-tests", action="store_true")
    c.add_argument("--test-runner", choices=["pytest", "unittest"], default="pytest")
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
    v = sub.add_parser("verify-run")
    v.add_argument("--run-id", required=True)
    v.add_argument("--receipt", required=True)
    v.add_argument("--atira", required=True)
    v.set_defaults(func=cmd_verify_run)
    args = ap.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
