"""Snapshot-bound decisions and exact, reversible evidence views."""
import hashlib
import json
import re
from dataclasses import dataclass, field

REF = re.compile(r'\[(?:f\d+)?e\d+\]')
BOOLEAN_FIELDS = frozenset({'is_current', 'usable_stroke', 'status_verified',
                           'is_approval', 'is_draft_or_preview', 'participation_registered'})


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:20]


@dataclass
class Coverage:
    scope: str
    sources: tuple[str, ...] = ()
    complete: bool = False
    gaps: tuple[str, ...] = ()
    ordering: str = ''


@dataclass
class Observation:
    id: str
    url: str
    text: str
    regions: dict[str, str] = field(default_factory=dict)
    tables: dict[str, list[list[str]]] = field(default_factory=dict)
    controls: dict[str, str] = field(default_factory=dict)
    changed: bool = True
    errors: list[str] = field(default_factory=list)

    @classmethod
    def capture(cls, text: str, url: str, previous=None):
        snapshot = cls(digest([url, text]), url, text)
        snapshot.changed = previous is None or snapshot.id != previous.id
        region, table = [], []
        def flush_region():
            if region:
                snapshot.regions['r' + str(len(snapshot.regions) + 1)] = '\n'.join(region)
                region.clear()
        def flush_table():
            if table:
                snapshot.tables['t' + str(len(snapshot.tables) + 1)] = list(table)
                table.clear()
        for line in text.splitlines():
            for match in REF.finditer(line):
                snapshot.controls[match[0][1:-1]] = line
            if line.startswith('| ') and line.endswith(' |'):
                # Tables with embedded separators in cells stay a model task.
                table.append([cell.strip() for cell in line[2:-2].split(' | ')])
            else:
                flush_table()
            if len('\n'.join(region)) + len(line) > 2400:
                flush_region()
            region.append(line)
        flush_table()
        flush_region()
        return snapshot

    def source_for_quote(self, quote):
        sources = set()
        for match in re.finditer(re.escape(quote), self.text):
            headers = list(re.finditer(r'^=== frame \d+: (.+?) ===$', self.text[:match.start()], re.M))
            sources.add(headers[-1][1] if headers else self.url)
        if len(sources) != 1:
            raise ValueError('quote is absent or ambiguous across source frames')
        return next(iter(sources))

    def copy_span(self, start, end):
        """Copy an exact model-selected span, never infer which text is relevant."""
        if not isinstance(start, str) or not isinstance(end, str) or not start or not end:
            raise ValueError('copy span needs nonempty exact boundaries')
        if self.text.count(start) != 1 or self.text.count(end) != 1:
            raise ValueError('copy boundaries are absent or ambiguous')
        first, last = self.text.index(start), self.text.index(end)
        if last < first:
            raise ValueError('copy boundaries are reversed')
        value = self.text[first:last + len(end)]
        if re.search(r'^=== frame \d+: ', value, re.M):
            raise ValueError('copy span crosses source frames')
        return value

    def view(self, limit=6500, sample_rows=3):
        """Include controls and exact content regions; omitted regions stay addressable."""
        sample_rows = max(1, min(3, sample_rows))
        lines = [f'snapshot={self.id}; changed={self.changed}; URL={self.url}']
        table_lines = {'| ' + ' | '.join(row) + ' |' for rows in self.tables.values() for row in rows[1:]}
        lines.extend(dict.fromkeys(line for line in self.controls.values() if line not in table_lines))
        for key, rows in self.tables.items():
            for row in rows[1:1 + sample_rows]:
                lines.append(f'{key} sample: ' + ' | '.join(row))
            for row in rows[1 + sample_rows:]:
                # Row controls remain observable, without repeatedly copying all
                # business values. The full row remains in its evidence region.
                for cell in row:
                    if REF.search(cell):
                        lines.append(f'{key} row control: {cell}')
        lines.extend(f'table {key}: header={rows[0]!r}; rows={len(rows)-1}' for key, rows in self.tables.items())
        for key, rows in self.tables.items():
            if len(rows) > 1 + sample_rows:
                handles = [region for region, text in self.regions.items()
                           if any('| ' + ' | '.join(row) + ' |' in text for row in rows[1 + sample_rows:])]
                lines.append(f'{key}: rows {1 + sample_rows}–{len(rows)-1} values omitted; read_region ' + ', '.join(handles))
        used = sum(len(x) for x in lines)
        for key, text in self.regions.items():
            # Interactive lines were already listed; removing duplicate copies
            # does not change identifiers, numerical punctuation or source text.
            content = '\n'.join(line for line in text.splitlines() if not REF.search(line) and line not in table_lines)
            if used + len(content) <= limit:
                lines.append(f'[{key}]\n{content}')
                used += len(content)
            else:
                lines.append(f'[{key}: {len(text)} chars omitted; read_region or extract to read completely]')
        return '\n'.join(lines)


@dataclass
class Decision:
    snapshot_id: str
    facts: list[dict] = field(default_factory=list)
    actions: list[tuple[str, dict]] = field(default_factory=list)
    bindings: list[dict] = field(default_factory=list)
    coverage: list[dict] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @classmethod
    def parse(cls, data, snapshot_id, allowed):
        if not isinstance(data, dict) or data.get('snapshot_id', snapshot_id) != snapshot_id:
            raise ValueError('decision belongs to another observation')
        actions, errors = [], []
        for action in (data.get('actions') or data.get('do') or [])[:8]:
            if not isinstance(action, dict):
                errors.append('action must be an object')
                continue
            action = dict(action)
            name = action.pop('a', action.pop('name', ''))
            if name not in allowed:
                errors.append('unknown action ' + str(name))
                continue
            actions.append((name, action))
        return cls(snapshot_id, data.get('facts') or [], actions, data.get('bindings') or [],
                   data.get('coverage') or [], data.get('gaps') or [], errors)


@dataclass
class ActionResult:
    applied: list[str] = field(default_factory=list)
    readbacks: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    next_snapshot: str = ''


def table_data(observation: Observation, binding: dict):
    """Parse only the table and semantic fields chosen by the model."""
    rows = observation.tables.get(binding.get('table'))
    if not rows or len(rows) < 2:
        raise ValueError('selected table has no records')
    columns, headers = binding.get('columns') or {}, rows[0]
    if binding.get('headers') != headers:
        raise ValueError('table headers changed or were not verified')
    if len(headers) != len(set(headers)):
        raise ValueError('ambiguous duplicate table headers')
    if not columns or any(not isinstance(i, int) or isinstance(i, bool) or i < 0 or i >= len(headers)
                          for i in columns.values()):
        raise ValueError('invalid column mapping')
    records = []
    for i, row in enumerate(rows[1:], 1):
        if len(row) != len(headers):
            raise ValueError('irregular table must be read by the model')
        record = {key: row[index] for key, index in columns.items()}
        for key in BOOLEAN_FIELDS.intersection(record):
            flag = record[key].strip().casefold()
            if flag in ('true', 'yes', 'ja', '1'):
                record[key] = True
            elif flag in ('false', 'no', 'nein', '0'):
                record[key] = False
            else:
                raise ValueError('ambiguous boolean table value; interpret with the model')
        records.append(record)
    data = dict(binding.get('context') or {})
    data[str(binding['list_field'])] = records
    return data
