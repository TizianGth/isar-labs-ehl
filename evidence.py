"""Central, append-only store for pages and documents selected by the research agent."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EvidenceStore:
    """Persist source-labelled observations so later reasoning uses one shared evidence set."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("EVIDENCE_PATH", "evidence.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: list[dict[str, Any]] = []
        if self.path.is_file():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(saved, dict) and isinstance(saved.get("records"), list):
                    self.records = saved["records"]
            except (OSError, ValueError):
                # A malformed prior artifact should not prevent a fresh run from collecting evidence.
                self.records = []

    def add(self, *, site_id: str, url: str, title: str, content: str,
            kind: str = "page", metadata: dict[str, Any] | None = None) -> bool:
        content = content.strip()
        if not content:
            return False
        digest = hashlib.sha256(f"{site_id}\0{url}\0{content}".encode("utf-8")).hexdigest()
        if any(record.get("id") == digest for record in self.records):
            return False
        self.records.append({
            "id": digest,
            "site_id": site_id,
            "url": url,
            "title": title,
            "kind": kind,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "content": content,
            "metadata": metadata or {},
        })
        self.save()
        return True

    def save(self) -> None:
        payload = {"version": 1, "records": self.records}
        fd, temporary = tempfile.mkstemp(prefix=self.path.name, suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def manifest(self, per_record_chars: int = 360, max_records: int = 24) -> list[dict[str, str]]:
        """Compact index for model planning; full source text remains in the JSON artifact."""
        return [
            {"site_id": record["site_id"], "title": record["title"], "url": record["url"],
             "kind": record["kind"], "excerpt": record["content"][:per_record_chars]}
            for record in self.records[-max_records:]
        ]
