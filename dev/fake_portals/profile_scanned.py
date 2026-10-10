"""Local scanned-document UI variant; no scoring or agent dependencies.

The page's normal rendering JavaScript paints fixture facts onto a canvas. Those
facts remain visually available, while DOM-only text extraction cannot read the
sheet. This deliberately exercises a visual fallback, not challenge fidelity.
"""
from html import unescape
from html.parser import HTMLParser
import json
import re

_SCHEMA_NAMES = (
    'bore_mm', 'stroke_mm', 'pressure_bar', 'mounting', 'certification',
    'catalog_group', 'pieces_per_set', 'unrestricted_stock', 'inspection_stock',
    'blocked_stock', 'lead_time_stock_weeks', 'lead_time_production_weeks',
    'pieces_per_carton', 'surcharge_mass_g', 'price_index_key', 'allocation_weight_g',
)
_SCHEMA_SUFFIX = re.compile(r'\s*\((?:' + '|'.join(map(re.escape, _SCHEMA_NAMES)) + r')\)')
_TABLE = re.compile(r'<table\b[^>]*>.*?</table\s*>', re.S | re.I)


class _SheetTable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self._row = []
        elif tag in ('td', 'th'):
            self._cell = []
        elif tag == 'br' and self._cell is not None:
            self._cell.append('\n')

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self._cell is not None:
            raw = ''.join(self._cell)
            value = '\n'.join(' '.join(part.split()) for part in raw.split('\n'))
            if self._row is not None:
                self._row.append(_SCHEMA_SUFFIX.sub('', value).strip())
            self._cell = None
        elif tag == 'tr' and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _canvas_sheet(table_html, sheet_id, price_sheet=False):
    parser = _SheetTable()
    parser.feed(table_html)
    if not parser.rows:
        return table_html
    if price_sheet:
        headers, *records = parser.rows
        lines = ['COMMERCIAL CONDITIONS — SCANNED ATTACHMENT', 'Published values; original numeric formats retained.']
        for index, record in enumerate(records, 1):
            lines.append(f'Condition record {index}')
            for heading, value in zip(headers, record):
                lines.append(f'{heading}: {value}')
    else:
        lines = ['TECHNICAL REQUIREMENT — SCANNED ATTACHMENT', 'Current controlled document; dimensional units as printed.']
        lines.extend(' : '.join(row) for row in parser.rows)
    # Encoding '<' prevents user-authored fixture strings from terminating script.
    payload = json.dumps(lines, ensure_ascii=True).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return f'''<figure class="scan-sheet" style="margin:18px 0; overflow:auto; border:1px solid #77828b; background:#eceff1; padding:10px;">
<figcaption style="padding:8px;font-size:16px">Scanned attachment. Read the document image below; browser screenshots are supported.</figcaption>
<canvas id="{sheet_id}" role="img" aria-label="Scanned document attachment" style="display:block;width:960px;max-width:100%;height:auto;background:white"></canvas>
<noscript>This document image requires JavaScript. Enable JavaScript to view the attachment.</noscript>
</figure>
<script>
(function(){{
 const canvas=document.getElementById('{sheet_id}'); const context=canvas.getContext('2d');
 const source={payload}; const width=960; const margin=30; const lineHeight=29;
 context.font='19px Arial, sans-serif';
 const wrapped=[];
 source.forEach((entry,entryIndex)=>{{
  entry.split('\\n').forEach(paragraph=>{{
   let line='';
   paragraph.split(/\\s+/).forEach(word=>{{
    const candidate=line ? line+' '+word : word;
    if(line && context.measureText(candidate).width>width-margin*2){{wrapped.push(line);line=word;}}
    else line=candidate;
   }});
   wrapped.push(line);
  }});
  if(entryIndex===1) wrapped.push('');
 }});
 canvas.width=width; canvas.height=margin*2+wrapped.length*lineHeight;
 context.fillStyle='#fffdf7';context.fillRect(0,0,canvas.width,canvas.height);
 context.strokeStyle='#b0aa97';context.strokeRect(12,12,canvas.width-24,canvas.height-24);
 context.font='19px Arial, sans-serif';context.fillStyle='#111';
 wrapped.forEach((line,index)=>context.fillText(line,margin,margin+23+index*lineHeight));
}})();
</script>'''


def transform_html(html: str, role: str) -> str:
    """Replace current requirement/ERP condition tables with readable canvases.

    Navigation, dates outside sheets, historical revisions, forms, and signing
    pages are preserved. No fixture data or requirement values are hardcoded.
    """
    if role not in ('specification', 'erp'):
        return html
    sheet_number = 0
    heading = re.search(r'<h2\b[^>]*>(.*?)</h2\s*>', html, flags=re.S | re.I)
    historical = bool(heading and re.search(r'historisch|historical|superseded|decoy', heading.group(1), re.I))

    def rewrite(match):
        nonlocal sheet_number
        table = match.group(0)
        current_requirement = role == 'specification' and not historical and 'bore_mm' in table and 'stroke_mm' in table
        price_sheet = role == 'erp' and all(label in table for label in ('FX-Schlüssel', 'Format')) and (
            'Preiseinheit' in table or 'Price unit' in table)
        if not (current_requirement or price_sheet):
            return table
        sheet_number += 1
        return _canvas_sheet(table, f'scanned-{role}-{sheet_number}', price_sheet)

    transformed = _TABLE.sub(rewrite, html)
    # Drop developer schema suffixes only from printed text. Do not rewrite
    # attributes, input names, script strings, identifiers, or values.
    transformed = re.sub(r'>([^<>]*)<', lambda m: '>' + _SCHEMA_SUFFIX.sub('', m.group(1)) + '<', transformed)
    return transformed
