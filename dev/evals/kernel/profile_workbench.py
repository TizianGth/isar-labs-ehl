"""A local enterprise workbench UI over the modern, unchanged portal kernel.

Only user-selected GET links use partial navigation. Forms remain native browser
submissions. Q&A loads one rendered next-page link per deliberate click, and ERP
detail tabs are behind a native disclosure. This module knows no fixture answers.
"""

import re


STYLE = r"""
<style id="workbench-style">
body {background:#edf1f5;color:#172b4d}
.app-header {padding-left:24px;background:#173451}
.workbench-nav {position:fixed;top:0;bottom:0;left:0;width:205px;box-sizing:border-box;
 padding:100px 18px 24px;border-right:1px solid #c2cbd6;background:#fff;overflow:auto;z-index:2}
.workbench-nav a {display:block;margin:0 0 12px;padding:10px;border-radius:5px}
.workbench-nav a.active {background:#e8f0fb}
body > .app-header, body > .main-wrap, body > .workbench-status {margin-left:205px}
.main-wrap {max-width:1180px}
.card {border-radius:2px;box-shadow:none}
.workbench-status {padding:8px 24px;color:#486581;font-size:13px;min-height:16px}
.workbench-material {border:1px solid #b6c7d8;border-radius:4px;margin-top:16px}
.workbench-material > summary {padding:16px;cursor:pointer;background:#e7edf4;font-weight:600}
.workbench-material > div {padding:16px}
[role=tab][aria-selected=true] {background:#173451;color:white}
[data-workbench-loadmore] {display:inline-block;border:1px solid #7890a6;padding:12px;
 background:#eef3f8;color:#173451;cursor:pointer}
[aria-busy=true] {opacity:.72}
@media(max-width:760px) {
 .workbench-nav {position:static;width:auto;padding:12px}
 .workbench-nav a {display:inline-block;margin:3px}
 body > .app-header,body > .main-wrap,body > .workbench-status {margin-left:0}
}
</style>
"""


SCRIPT = r"""
<script id="workbench-controller">
(() => {
  if (window.__workbenchController) return;
  window.__workbenchController = true;
  let navigation = 0;
  let activeRequest = null;
  const status = message => {
    const node = document.querySelector('.workbench-status');
    if (node) node.textContent = message;
  };
  const showTab = button => {
    const group = button.closest('[role="tablist"]');
    if (!group) return;
    const scope = group.closest('.workbench-material') || document;
    group.querySelectorAll('[role="tab"]').forEach(item => {
      const selected = item === button;
      item.setAttribute('aria-selected', String(selected));
      item.tabIndex = selected ? 0 : -1;
    });
    scope.querySelectorAll('[role="tabpanel"]').forEach(panel => {
      panel.style.display = panel.id === button.getAttribute('aria-controls') ? 'block' : 'none';
    });
  };
  const initialize = () => {
    document.querySelectorAll('[role="tablist"]').forEach(group => {
      const selected = group.querySelector('[role="tab"][aria-selected="true"]') || group.querySelector('[role="tab"]');
      if (selected) showTab(selected);
    });
  };
  const parsePage = async (url, signal) => {
    const response = await fetch(url, {credentials:'same-origin', signal});
    if (!response.ok || !(response.headers.get('content-type') || '').includes('text/html')) {
      throw new Error('Unable to load the selected page');
    }
    return {page: new DOMParser().parseFromString(await response.text(), 'text/html'), url: response.url};
  };
  const navigate = async (url, push) => {
    const sequence = ++navigation;
    if (activeRequest) activeRequest.abort();
    activeRequest = new AbortController();
    status('Loading selected page…');
    try {
      const result = await parsePage(url, activeRequest.signal);
      if (sequence !== navigation) return;
      document.body.replaceWith(result.page.body);
      document.title = result.page.title;
      if (push) history.pushState(null, '', result.url);
      initialize();
      window.scrollTo(0, 0);
      status('Selected page loaded.');
    } catch (error) {
      if (error.name !== 'AbortError' && sequence === navigation) location.assign(url);
    }
  };
  const loadMore = async anchor => {
    if (anchor.getAttribute('aria-busy') === 'true') return;
    const currentTable = document.querySelector('[data-workbench-qa]');
    if (!currentTable) {location.assign(anchor.href);return;}
    const startingBody = document.body;
    anchor.setAttribute('aria-busy', 'true');
    status('Loading the next published Q&A page…');
    try {
      const result = await parsePage(anchor.href);
      if (document.body !== startingBody) return;
      const nextTable = result.page.querySelector('[data-workbench-qa]');
      if (!nextTable) {location.assign(result.url);return;}
      const targetRows = currentTable.tBodies[0];
      const incomingRows = nextTable.tBodies[0];
      if (!targetRows || !incomingRows) throw new Error('Missing Q&A rows');
      Array.from(incomingRows.rows).forEach(row => targetRows.append(document.importNode(row, true)));
      const following = result.page.querySelector('[data-workbench-loadmore]');
      if (following) {
        anchor.href = new URL(following.getAttribute('href'), result.url).href;
        anchor.removeAttribute('aria-busy');
      } else {
        const done = document.createElement('p');
        done.textContent = 'All published Q&A pages have been loaded.';
        anchor.replaceWith(done);
      }
      status('The selected Q&A page has been appended.');
    } catch (error) {
      if (document.body !== startingBody) return;
      anchor.removeAttribute('aria-busy');
      status('Could not append Q&A. Follow the page link to retry.');
      location.assign(anchor.href);
    }
  };
  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const tab = event.target.closest('[data-workbench-panel]');
    if (tab) {event.preventDefault();showTab(tab);return;}
    const anchor = event.target.closest('a[href]');
    if (!anchor || anchor.hasAttribute('download') || (anchor.target && anchor.target !== '_self')) return;
    const destination = new URL(anchor.href, location.href);
    if (destination.origin !== location.origin || !['http:','https:'].includes(destination.protocol)) return;
    if (destination.pathname === location.pathname && destination.search === location.search && destination.hash) return;
    event.preventDefault();
    if (anchor.hasAttribute('data-workbench-loadmore')) loadMore(anchor);
    else navigate(destination.href, true);
  });
  document.addEventListener('keydown', event => {
    const current = event.target.closest('[data-workbench-panel]');
    if (!current || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    const tabs = Array.from(current.closest('[role="tablist"]').querySelectorAll('[role="tab"]'));
    let index = tabs.indexOf(current);
    index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 :
      (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault();showTab(tabs[index]);tabs[index].focus();
  });
  window.addEventListener('popstate', () => navigate(location.href, false));
  initialize();
})();
</script>
"""


def transform_html(html: str, role: str) -> str:
    """Transform complete modern-kernel HTML without changing its form values.

    No paths are inferred: the parent route wrapper can rewrite opaque URLs
    either before or after this function. Native form methods, inputs, actions,
    and signing confirmation remain untouched. The ERP markup adapter targets
    the kernel's tablist/tabpanel structure; other ERP pages stay normal.
    """
    if role not in ('tender', 'specification', 'erp', 'mail'):
        raise ValueError('Unknown portal role')
    if 'id="workbench-controller"' in html:
        return html
    if not re.search(r'</head\s*>', html, re.I) or not re.search(r'</body\s*>', html, re.I):
        raise ValueError('Workbench transformation requires a complete HTML document')
    html = html.replace('Profile: Modern', 'Operations workbench')
    html = html.replace('<nav class="app-nav">',
                        '<nav class="app-nav workbench-nav" aria-label="Application navigation">')
    # Inline functions from fetched pages do not execute. Stable delegated
    # listeners operate on the actual tab selected by the user after navigation.
    html = re.sub(r'onclick="switchTab\(this,\s*(\d+)\)"',
                  lambda match: f'data-workbench-panel="{match[1]}" aria-controls="panel-{match[1]}"', html)
    if role == 'erp':
        tab_start = html.find('<div role="tablist">')
        if tab_start != -1:
            panels = list(re.finditer(r'<section\s+role="tabpanel"[^>]*>.*?</section>', html[tab_start:], re.S))
            if panels:
                end = tab_start + panels[-1].end()
                html = (html[:tab_start] + '<details class="workbench-material">'
                        '<summary>Open material workspace: technical data, stock and pricing</summary><div>'
                        + html[tab_start:end] + '</div></details>' + html[end:])
    if role == 'tender' and re.search(r'<h2>Questions and answers\s*\(', html):
        html = re.sub(r'(<h2>Questions and answers\s*\(.*?</h2>\s*)<table>',
                      r'\1<table data-workbench-qa="true">', html, count=1, flags=re.S)
        # Preserve the rendered link's exact escaped href. No additional pages
        # are requested before a click, and no page number is manufactured.
        html = re.sub(r'<a\b([^>]*\bhref="[^"]*"[^>]*)>Next page</a>',
                      r'<a\1 data-workbench-loadmore="true">Load more questions (next page)</a>', html)
    html = re.sub(r'</head\s*>', STYLE + '</head>', html, count=1, flags=re.I)
    html = re.sub(r'(<body\b[^>]*>)',
                  r'\1<div class="workbench-status" role="status" aria-live="polite"></div>',
                  html, count=1, flags=re.I)
    return re.sub(r'</body\s*>', SCRIPT + '</body>', html, count=1, flags=re.I)
