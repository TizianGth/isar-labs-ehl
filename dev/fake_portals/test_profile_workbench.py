"""Deterministic workbench transform and actual Chromium interaction checks."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import unittest
from urllib.parse import parse_qs, urlparse

from profile_workbench import transform_html
from server import render_layout

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def page_html(body, role='tender'):
    return transform_html(render_layout('Synthetic workbench', body, 'modern', role,
                                       nav_links=[('Dashboard', '/start')]), role)


class WorkbenchTransformTests(unittest.TestCase):
    def test_forms_confirmation_and_escaped_values_are_preserved(self):
        form = ('<form method="POST" action="/opaque/sign?x=1&amp;y=2" '
                'onsubmit="return confirm(\'Submit?\');">'
                '<input name="company" value="A &amp; B &lt;test&gt;">'
                '<input name="amount" value="1.234,50">'
                '<input name="tan" value="731092">'
                '<input type="checkbox" name="terms" checked>'
                '<button type="submit">Sign</button></form>')
        html = page_html(form)
        self.assertIn(form, html)
        self.assertIn('aria-label="Application navigation"', html)
        self.assertNotIn('731092', html[:html.index('<form')])

    def test_qa_preserves_actual_next_href_and_does_not_encode_answers(self):
        html = page_html('<h2>Questions and answers (Page 1 of 3)</h2><table>'
                         '<tbody><tr><td>Question &lt;1&gt;</td></tr></tbody></table>'
                         '<a href="/opaque/feed?token=abc&amp;page=2">Next page</a>')
        self.assertIn('href="/opaque/feed?token=abc&amp;page=2" data-workbench-loadmore', html)
        self.assertIn('<table data-workbench-qa="true">', html)
        self.assertNotIn('token=abc&amp;page=3', html)
        self.assertIn("parsePage(anchor.href)", html)

    def test_erp_accordion_preserves_data_and_has_delegated_tabs(self):
        html = page_html('<h3>Technical facts</h3><p>38 mm &amp; 175 bar</p>'
                         '<h3>Stock</h3><p>10 released; 80 inspection</p>'
                         '<h3>Prices</h3><p>90,00 EUR per 1 piece</p>', 'erp')
        self.assertIn('<details class="workbench-material">', html)
        self.assertNotIn('<details class="workbench-material" open', html)
        self.assertIn('data-workbench-panel="2" aria-controls="panel-2"', html)
        self.assertNotIn('onclick="switchTab', html)
        for fact in ('38 mm &amp; 175 bar', '10 released; 80 inspection', '90,00 EUR per 1 piece'):
            self.assertIn(fact, html)

    def test_no_qa_pagination_rewrite_for_other_roles(self):
        html = page_html('<h2>Questions and answers (Page 1 of 3)</h2><table></table>'
                         '<a href="/next">Next page</a>', 'mail')
        self.assertIn('<a href="/next">Next page</a>', html)
        self.assertNotIn('<table data-workbench-qa=', html)

    def test_idempotent_and_complete_document(self):
        html = page_html('<p>Mailbox</p>', 'mail')
        self.assertEqual(transform_html(html, 'mail'), html)
        self.assertEqual(html.count('id="workbench-controller"'), 1)
        self.assertTrue(html.endswith('</html>'))
        with self.assertRaises(ValueError):
            transform_html('<p>fragment</p>', 'mail')
        with self.assertRaises(ValueError):
            transform_html(html, 'unknown')


@unittest.skipIf(sync_playwright is None, 'Existing Playwright environment is needed for browser checks')
class WorkbenchBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []
        cls.posts = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send_html(self, body, role='tender'):
                data = page_html(body, role).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                cls.requests.append(self.path)
                parsed = urlparse(self.path)
                if parsed.path == '/qa':
                    page = int(parse_qs(parsed.query).get('page', ['1'])[0])
                    rows = ''.join(f'<tr><td>Answer #{number}</td></tr>'
                                   for number in range(page * 2 - 1, page * 2 + 1))
                    following = f'<a href="/qa?page={page + 1}">Next page</a>' if page < 3 else ''
                    self.send_html(f'<h2>Questions and answers (Page {page} of 3)</h2>'
                                   f'<table><tbody>{rows}</tbody></table>{following}')
                elif parsed.path == '/detail':
                    self.send_html('<h3>Technical facts</h3><p>Stroke 120 mm</p>'
                                   '<h3>Stock</h3><p>Released stock 10</p>'
                                   '<h3>Prices</h3><p>Tier 90,00 EUR</p>', 'erp')
                elif parsed.path == '/proposal':
                    self.send_html('<form method="POST" action="/sign" '
                                   'onsubmit="return confirm(\'Submit binding proposal?\');">'
                                   '<label>Amount <input name="amount" value="1.234,50"></label>'
                                   '<label>Signing TAN <input name="tan" value="731092"></label>'
                                   '<button>Submit binding proposal</button></form>')
                else:
                    self.send_html('<a href="/qa?page=1">Questions</a> '
                                   '<a href="/detail">Material detail</a> '
                                   '<a href="/proposal">Proposal</a>')

            def do_POST(self):
                cls.posts.append((self.path, parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())))
                self.send_html('<h2>Submission receipt</h2>')

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f'http://127.0.0.1:{cls.server.server_port}'
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        type(self).requests.clear()
        type(self).posts.clear()
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.origin + '/start')
        self.page.evaluate('window.testDocumentIdentity = document')

    def tearDown(self):
        self.context.close()

    def test_spa_navigation_erp_disclosure_and_tabs_after_body_replacement(self):
        self.page.get_by_role('link', name='Material detail').click()
        self.page.wait_for_url('**/detail')
        self.assertTrue(self.page.evaluate('window.testDocumentIdentity === document'))
        self.assertFalse(self.page.get_by_text('Stroke 120 mm').is_visible())
        self.page.locator('summary').click()
        self.assertTrue(self.page.get_by_text('Stroke 120 mm').is_visible())
        self.page.get_by_role('tab', name='Prices').click()
        self.assertTrue(self.page.get_by_text('Tier 90,00 EUR').is_visible())
        self.assertFalse(self.page.get_by_text('Stroke 120 mm').is_visible())
        self.page.get_by_role('tab', name='Prices').press('Home')
        self.assertTrue(self.page.get_by_text('Stroke 120 mm').is_visible())
        self.page.go_back()
        self.page.get_by_role('link', name='Material detail').wait_for()
        self.assertTrue(self.page.evaluate('window.testDocumentIdentity === document'))
        self.assertEqual(self.errors, [])

    def test_load_more_follows_one_actual_next_link_per_click(self):
        self.page.get_by_role('link', name='Questions', exact=True).click()
        self.page.wait_for_url('**/qa?page=1')
        self.assertEqual(self.page.locator('[data-workbench-qa] tbody tr').count(), 2)
        self.assertNotIn('/qa?page=2', self.requests)
        self.page.get_by_role('link', name='Load more questions (next page)').click()
        self.page.get_by_text('Answer #4', exact=True).wait_for()
        self.assertEqual(self.page.locator('[data-workbench-qa] tbody tr').count(), 4)
        self.assertNotIn('/qa?page=3', self.requests)
        self.page.get_by_role('link', name='Load more questions (next page)').click()
        self.page.get_by_text('Answer #6', exact=True).wait_for()
        self.assertEqual(self.page.locator('[data-workbench-qa] tbody tr').count(), 6)
        self.assertEqual(self.page.locator('[data-workbench-loadmore]').count(), 0)
        self.assertEqual(self.requests.count('/qa?page=2'), 1)
        self.assertEqual(self.requests.count('/qa?page=3'), 1)
        self.assertEqual(self.errors, [])

    def test_direct_goto_loses_spa_document_state_while_click_preserves_it(self):
        self.page.get_by_role('link', name='Material detail').click()
        self.page.wait_for_url('**/detail')
        self.assertTrue(self.page.evaluate('window.testDocumentIdentity === document'))
        self.page.goto(self.origin + '/detail')
        self.assertTrue(self.page.evaluate('typeof window.testDocumentIdentity === "undefined"'))
        self.assertEqual(self.errors, [])

    def test_signing_still_uses_native_post_form_and_confirmation(self):
        self.page.get_by_role('link', name='Proposal', exact=True).click()
        self.page.wait_for_url('**/proposal')
        self.page.on('dialog', lambda dialog: dialog.accept())
        self.page.get_by_role('button', name='Submit binding proposal').click()
        self.page.get_by_role('heading', name='Submission receipt').wait_for()
        self.assertEqual(self.posts, [('/sign', {'amount': ['1.234,50'], 'tan': ['731092']})])
        self.assertIsNone(self.page.evaluate('window.testDocumentIdentity'))
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
