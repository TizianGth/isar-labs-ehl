"""Observation: every frame of a page serialized by observe.js into one compact text for the model.

Refs look like `e12` (main frame) or `f2e7` (frame 2); `frame_of(page, ref)` finds the frame again.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import Frame, Page

JS = (Path(__file__).with_name("observe.js")).read_text(encoding="utf-8")
MAX_CHARS = 24_000  # ≈ 6k tokens


@dataclass
class Observation:
    url: str
    title: str
    text: str
    frames: dict[str, Frame] = field(default_factory=dict)  # prefix -> frame ("" = main)

    def render(self, max_chars: int = MAX_CHARS, offset: int = 0) -> str:
        body = self.text[offset:]
        cut = ""
        if len(body) > max_chars:
            # cut at a line boundary
            end = body.rfind("\n", 0, max_chars)
            end = end if end > max_chars // 2 else max_chars
            cut = (f"\n[… {len(body) - end} weitere Zeichen gekürzt — observe(offset={offset + end}) "
                   "oder observe(scope=<ref>) für einen Ausschnitt]")
            body = body[:end]
        return f"URL: {self.url}\nTitel: {self.title}\n{body}{cut}"


def frame_prefixes(page: Page) -> dict[str, Frame]:
    """Stable per page load: main frame "" and child frames f1, f2, … in document order."""
    frames = {"": page.main_frame}
    for i, f in enumerate([f for f in page.frames if f != page.main_frame], start=1):
        frames[f"f{i}"] = f
    return frames


def frame_of(page: Page, ref: str) -> Frame | None:
    prefix = ref[: ref.index("e")] if "e" in ref else ""
    return frame_prefixes(page).get(prefix)


async def observe(page: Page, scope: str | None = None) -> Observation:
    frames = frame_prefixes(page)
    parts: list[str] = []
    title = ""
    for prefix, frame in frames.items():
        if scope and frame_of(page, scope) is not frame:
            continue
        try:
            res = await frame.evaluate(JS, {"prefix": prefix, "scope": scope})
        except Exception as e:  # detached / navigating frame
            parts.append(f"=== frame {prefix or 'main'} {frame.url} — nicht lesbar ({type(e).__name__}) ===")
            continue
        if res.get("error"):
            parts.append(res["error"])
            continue
        if prefix == "":
            title = res.get("title", "")
            parts.append(res["text"])
        else:
            parent = next((p for p, f in frames.items() if f == frame.parent_frame), "")
            head = f"=== frame {prefix} (in {parent or 'main'}) {frame.url} ==="
            parts.append(head + "\n" + (res["text"] or "(leer)"))
    return Observation(url=page.url, title=title, text="\n".join(parts), frames=frames)


def diff(before: str, after: str, max_lines: int = 60) -> str | None:
    """Only the changed lines, if that is much shorter than the whole page; else None."""
    a, b = before.splitlines(), after.splitlines()
    changed = [l for l in difflib.ndiff(a, b) if l[:2] in ("+ ", "- ")]
    if not changed:
        return "(keine sichtbare Änderung)"
    if len(changed) > max_lines or len(changed) > len(b) // 2:
        return None
    return "\n".join(changed)
