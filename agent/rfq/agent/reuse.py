"""Run-local sharing and explicitly model-selected semantic templates."""
import asyncio
import copy
import re
from urllib.parse import urlparse
from .records import REF, digest, table_data


class SourceReads:
    """Share concurrent reads, never serve a completed live URL as fresh evidence."""
    def __init__(self, limit=4):
        self.pending = {}
        self.limit = limit
        self.gates = {}
        self.stats = {"loads": 0, "shared": 0}

    async def read(self, site, url, loader):
        key = (id(site.context), site.site_id, site.session_generation, url)
        task = self.pending.get(key)
        if task is None or task.done():
            async def load():
                gate = self.gates.setdefault(id(site.context), asyncio.Semaphore(self.limit))
                async with gate:
                    self.stats["loads"] += 1
                    return await loader()
            task = self.pending[key] = asyncio.create_task(load())
        else:
            self.stats["shared"] += 1
        try:
            return await asyncio.shield(task)
        finally:
            if task.done() and self.pending.get(key) is task:
                self.pending.pop(key, None)

    async def close(self):
        tasks = list(self.pending.values())
        self.pending.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def table_signature(snapshot, table, layout=False):
    """Reuse only on the same source/context, with identical headers and locale markers."""
    rows = snapshot.tables.get(table)
    if not rows:
        raise ValueError("table disappeared")
    header = "| " + " | ".join(rows[0]) + " |"
    source = snapshot.source_for_quote(header)
    # Other tables may contain revision/status/context used by this binding.
    target = "\n".join("| " + " | ".join(row) + " |" for row in rows)
    if snapshot.text.count(target) != 1:
        raise ValueError("table occurrence is ambiguous")
    surrounding = REF.sub("", snapshot.text.replace(target, "<selected-table>", 1))
    locales = sorted(set(re.findall(r"\b(?:[a-z]{2}-[A-Z]{2}|TT/MM|MM/TT)\b", str(rows))))
    numeric_style = set()
    for row in rows[1:]:
        for cell in row:
            if re.search(r'\b(?:de-DE|en-US)\b', cell):
                continue
            for number in re.findall(r'(?<!\w)[−+-]?\d[\d.,]*', cell):
                separators = ''.join(character for character in number if character in '.,')
                if separators:
                    last = max(number.rfind('.'), number.rfind(','))
                    numeric_style.add((tuple(dict.fromkeys(separators)), number[last], len(number)-last-1))
    page = snapshot.url
    if layout:
        # Only an explicit model authorization permits sharing semantics across
        # selected sources. Never generalize record/revision/authorization context.
        def origin(url):
            parsed = urlparse(url)
            return parsed.scheme + '://' + parsed.netloc
        page, source = origin(page), origin(source)
        surrounding = re.sub(r'(^=== frame \d+: )(.+?)( ===$)',
                             lambda match: match[1] + origin(match[2]) + match[3], surrounding, flags=re.M)
    return digest([page, source, table, surrounding, rows[0], locales, sorted(numeric_style), len(rows[0])])


class TableBindings:
    def __init__(self):
        self.templates = {}
        self.applied = set()
        self.stats = {"stored": 0, "hits": 0, "misses": 0}

    def remember(self, snapshot, binding, scope):
        if binding.get("reuse") is not True:
            return
        # A fixed context must be witnessed outside the changing selected rows.
        outside = "\n".join(line for line in snapshot.text.splitlines() if not line.startswith("| "))
        if any(not isinstance(value, str) or not value or value not in outside
               for value in (binding.get("context") or {}).values()):
            return
        # Validate before a template can enter the cache.
        table_data(snapshot, binding)
        layout = binding.get("reuse_scope") == "layout" and not binding.get("context")
        signature = table_signature(snapshot, binding["table"], layout)
        key = (scope, binding["kind"], signature, layout)
        self.templates[key] = copy.deepcopy(binding)
        self.applied.add((scope, snapshot.id, binding["kind"], binding["table"]))
        self.stats["stored"] += 1

    def candidates(self, snapshot, scope):
        for table in snapshot.tables:
            for (saved_scope, kind, saved_signature, layout), saved in list(self.templates.items()):
                try:
                    signature = table_signature(snapshot, table, layout)
                except ValueError:
                    continue
                if saved_scope != scope or saved_signature != signature:
                    continue
                stamp = (scope, snapshot.id, kind, table)
                if stamp in self.applied:
                    continue
                binding = {**copy.deepcopy(saved), "table": table}
                try:
                    table_data(snapshot, binding)
                except (ValueError, KeyError, TypeError):
                    self.stats["misses"] += 1
                    continue
                yield stamp, binding

    def accepted(self, stamp):
        self.applied.add(stamp)
        self.stats["hits"] += 1
