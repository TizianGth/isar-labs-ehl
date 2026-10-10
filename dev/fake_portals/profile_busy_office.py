"""Busy-office presentation layered over the synthetic legacy portal kernel.

No data/route knowledge lives here. Route rewriting should happen before this
transform: embedded srcdoc frames then contain the exact rendered navigation.
"""

from html import escape, unescape
import re


_STYLE = """
<style id="busy-office-style">
body { background:#d8dde4; color:#18212d; font-family:Arial,sans-serif; }
.bo-desk { background:#24354d; color:#fff; padding:10px 14px; display:flex;
  align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }
.bo-notice { background:#fff8dd; border:1px solid #bda75b; padding:10px;
  margin:10px 0; display:flex; align-items:center; justify-content:space-between; gap:12px; }
.bo-notice[hidden] { display:none; }
.bo-note { font-size:.85rem; color:#475569; margin:8px 0; }
.bo-helper { margin-left:8px; background:#e7effb; border:1px solid #7191b8;
  padding:5px 9px; cursor:pointer; }
.bo-workspace { width:100%; min-height:760px; border:2px solid #7d8fa6; background:#fff; }
#bo-feedback { border:1px solid #4b6485; max-width:400px; padding:24px; }
#bo-feedback::backdrop { background:rgba(20,30,45,.25); }
button:disabled { cursor:not-allowed; opacity:.6; }
</style>
"""

_SCRIPT = """
<script id="busy-office-controls">
function busyOfficeOpen(button) {
  const url = new URL(button.dataset.detailUrl, document.baseURI);
  if (url.origin !== location.origin) return;
  const helper = window.open(url.href, 'busy-office-material-helper',
    'popup,width=1040,height=850,resizable=yes,scrollbars=yes');
  if (!helper) {
    document.getElementById('bo-popup-status').textContent =
      'Popup blocked. Use the ordinary material Details link next to this button.';
  }
}
</script>
"""


def _nest_workspaces(html: str, role: str) -> str:
    if role not in ('specification', 'erp'):
        return html

    def wrap(match):
        original_frame = match[0]
        title = 'Technical document workspace' if role == 'specification' else 'ERP application workspace'
        # srcdoc is same-origin and deliberately NOT sandboxed: all existing
        # links, forms, session cookies and explicit popup actions keep working.
        inner = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                 '<title>' + title + '</title><style>body{margin:8px;font-family:Arial,sans-serif;}'
                 'iframe{width:100%;min-height:690px;border:1px solid #8798af}</style>'
                 '</head><body><p>Application workspace — navigate the embedded portal below.</p>'
                 + original_frame + '</body></html>')
        return ('<iframe class="bo-workspace" title="' + title + '" srcdoc="'
                + escape(inner, quote=True) + '"></iframe>')

    return re.sub(r'<iframe\b[^>]*>\s*</iframe>', wrap, html, flags=re.I)


def _add_material_helpers(html: str, role: str) -> str:
    if role != 'erp':
        return html

    def append_button(match):
        attrs, label = match[1], match[2]
        if label.strip() not in ('Details', 'Material details'):
            return match[0]
        href = re.search(r'\bhref\s*=\s*([\'"])(.*?)\1', attrs, re.I | re.S)
        if not href:
            return match[0]
        url = unescape(href[2])
        # A link supplied by the rendered application is the sole URL source.
        # Ignore absolute/network/javascript links instead of inventing routes.
        if not url.startswith('/') or url.startswith('//'):
            return match[0]
        return (match[0] + '<button type="button" class="bo-helper" '
                'data-detail-url="' + escape(url, quote=True) + '" '
                'onclick="busyOfficeOpen(this)">Open material / logistics helper window</button>')

    return re.sub(r'<a\b([^>]*)>(.*?)</a>', append_button, html, flags=re.I | re.S)


def transform_html(html: str, role: str) -> str:
    """Return complete HTML with office distractions and real navigation quirks.

    Authentication and signing stay at their original top-level locations.
    The maintenance banner is non-modal; feedback dialog opens only after a
    user action; ERP popup buttons supplement ordinary detail links.
    """
    if role not in ('tender', 'specification', 'erp', 'mail'):
        raise ValueError('Unknown portal role: ' + str(role))
    if 'id="busy-office-style"' in html:
        return html
    html = _add_material_helpers(html, role)
    html = _nest_workspaces(html, role)
    html = re.sub(r'</head\s*>', _STYLE + '\n</head>', html, count=1, flags=re.I)
    login = bool(re.search(r'<input\b[^>]*\btype\s*=\s*[\'"]password[\'"]', html, re.I))
    toolbar = ('<div class="bo-desk"><strong>Operations desktop — ' + escape(role.title())
               + '</strong><button type="button" disabled aria-disabled="true">'
               'Print queue unavailable (read only)</button></div>')
    notification = '' if login else (
        '<aside class="bo-notice" id="bo-maintenance" role="status">'
        '<span>Office notice: printer maintenance affects paper copies only. '
        'Portal documents, material data and digital signing remain available.</span>'
        '<button type="button" aria-label="Dismiss office notice" '
        'onclick="document.getElementById(\'bo-maintenance\').hidden=true">Dismiss notice</button></aside>')
    html = re.sub(r'(<body\b[^>]*>)', lambda m: m[1] + toolbar + notification,
                  html, count=1, flags=re.I)
    extra = '<p id="bo-popup-status" class="bo-note" role="status"></p>'
    if not login:
        extra += ('<footer class="bo-note"><button type="button" '
                  'onclick="document.getElementById(\'bo-feedback\').showModal()">'
                  'Optional portal feedback</button> — unrelated to your RFQ.</footer>'
                  '<dialog id="bo-feedback" aria-labelledby="bo-feedback-title">'
                  '<h2 id="bo-feedback-title">Optional office feedback</h2>'
                  '<p>This survey is unrelated to proposal submission. No response is required.</p>'
                  '<button type="button" onclick="document.getElementById(\'bo-feedback\').close()">'
                  'Close feedback</button></dialog>')
    return re.sub(r'</body\s*>', extra + _SCRIPT + '\n</body>', html, count=1, flags=re.I)
