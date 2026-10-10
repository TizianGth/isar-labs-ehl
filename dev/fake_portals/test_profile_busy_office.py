"""Deterministic contract tests for the busy-office presentation transform."""

from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import threading
import unittest

from profile_busy_office import transform_html
from server import render_layout


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)

    def by_tag(self, tag):
        return [attrs for name, attrs in self.tags if name == tag]


class BusyOfficeProfileTests(unittest.TestCase):
    def page(self, role, body):
        return render_layout('Synthetic application', body, profile='legacy', role=role)

    def test_complete_html_preserves_business_facts_and_signing_form(self):
        body = ('<p>SYN-RFQ-987: 2,50 EUR, REV-C current.</p>'
                '<form action="/opaque/sign" method="POST" '
                'onsubmit="return confirm(\'Submit binding proposal?\')">'
                '<input name="tan" value="4711"><input type="checkbox" name="accept_documents" checked>'
                '<button type="submit">Sign proposal</button></form>')
        html = transform_html(self.page('tender', body), 'tender')
        dom = Elements(html)
        self.assertIn(body, html)
        self.assertIn('SYN-RFQ-987: 2,50 EUR, REV-C current.', ''.join(dom.text))
        self.assertEqual(dom.by_tag('form')[0]['action'], '/opaque/sign')
        self.assertIn('confirm(', dom.by_tag('form')[0]['onsubmit'])
        self.assertEqual(dom.by_tag('input')[0]['value'], '4711')
        self.assertNotIn('open', dom.by_tag('dialog')[0])
        self.assertIn('<!DOCTYPE html>', html)

    def test_login_stays_top_level_without_notification_or_feedback(self):
        body = ('<form method="POST" action="/auth-new">'
                '<input name="username" value="demo">'
                '<input type="password" name="password" value="synthetic-only">'
                '<button type="submit">Sign In</button></form>')
        html = transform_html(self.page('specification', body), 'specification')
        dom = Elements(html)
        self.assertEqual(dom.by_tag('input')[1]['value'], 'synthetic-only')
        self.assertEqual(dom.by_tag('iframe'), [])
        self.assertEqual(dom.by_tag('dialog'), [])
        self.assertNotIn('id="bo-maintenance"', html)
        self.assertEqual(dom.by_tag('form')[0]['action'], '/auth-new')

    def test_nested_frames_preserve_rewritten_actual_source(self):
        for role in ('specification', 'erp'):
            with self.subTest(role=role):
                body = '<iframe src="/opaque/workspace?p=1&amp;q=2" height="600"></iframe>'
                html = transform_html(self.page(role, body), role)
                outer = Elements(html).by_tag('iframe')[0]
                self.assertNotIn('sandbox', outer)
                nested = Elements(outer['srcdoc'])
                self.assertEqual(nested.by_tag('iframe')[0]['src'], '/opaque/workspace?p=1&q=2')
                self.assertIn('Application workspace', ''.join(nested.text))
                self.assertEqual(len(nested.by_tag('iframe')), 1)

    def test_real_popup_url_is_taken_only_from_material_detail_anchor(self):
        body = ('<a href="/opaque/material?id=MAT-X&amp;view=full" class="btn">Details</a>'
                '<a href="/opaque/find">Back to catalogue</a>')
        html = transform_html(self.page('erp', body), 'erp')
        helpers = [b for b in Elements(html).by_tag('button') if 'data-detail-url' in b]
        self.assertEqual(len(helpers), 1)
        self.assertEqual(helpers[0]['data-detail-url'], '/opaque/material?id=MAT-X&view=full')
        self.assertEqual(helpers[0]['onclick'], 'busyOfficeOpen(this)')
        self.assertEqual(helpers[0]['type'], 'button')
        self.assertIn('window.open(url.href', html)
        self.assertIn(body.split('</a>')[0] + '</a>', html)
        self.assertNotIn('setTimeout', html)

    def test_no_popup_for_nonlocal_or_irrelevant_links(self):
        body = ('<a href="https://example.com">Details</a><a href="//example.com">Details</a>'
                '<a href="javascript:alert(1)">Details</a><a href="/documents">Documents</a>')
        html = transform_html(self.page('erp', body), 'erp')
        self.assertFalse(any('data-detail-url' in b for b in Elements(html).by_tag('button')))
        html = transform_html(self.page('mail', '<a href="/local">Details</a>'), 'mail')
        self.assertFalse(any('data-detail-url' in b for b in Elements(html).by_tag('button')))

    def test_notice_and_feedback_have_explicit_close_controls(self):
        html = transform_html(self.page('mail', '<p>Mail subject and sender</p>'), 'mail')
        dom = Elements(html)
        dismiss = [b for b in dom.by_tag('button') if b.get('aria-label') == 'Dismiss office notice']
        self.assertEqual(len(dismiss), 1)
        self.assertIn('.hidden=true', dismiss[0]['onclick'])
        self.assertTrue(any('.close()' in b.get('onclick', '') for b in dom.by_tag('button')))
        self.assertTrue(any('.showModal()' in b.get('onclick', '') for b in dom.by_tag('button')))
        self.assertFalse(any('open' in d for d in dom.by_tag('dialog')))
        self.assertTrue(any('disabled' in b for b in dom.by_tag('button')))

    def test_transform_is_idempotent_and_validates_role(self):
        html = transform_html(self.page('tender', '<p>Reference</p>'), 'tender')
        self.assertEqual(transform_html(html, 'tender'), html)
        with self.assertRaises(ValueError):
            transform_html(html, 'oracle')


@unittest.skipUnless(os.environ.get('FAKE_PORTALS_BROWSER_TESTS') == '1',
                     'Set FAKE_PORTALS_BROWSER_TESTS=1 in an existing Playwright environment')
class BusyOfficeBrowserTests(unittest.TestCase):
    def test_browser_frame_modal_notice_and_popup_interactions(self):
        from playwright.sync_api import sync_playwright

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == '/':
                    body = '<iframe src="/app"></iframe>'
                elif self.path == '/app':
                    body = ('<p>Material catalogue</p>'
                            '<a class="btn" href="/detail?id=MAT-X">Details</a>')
                else:
                    body = ('<h2>MAT-X material details</h2>'
                            '<p>Free stock 10 Stk, production 8 weeks.</p>')
                page = transform_html(render_layout('ERP', body, 'legacy', 'erp'), 'erp').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(page)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.get_by_role('button', name='Dismiss office notice').click()
                self.assertFalse(page.locator('#bo-maintenance').is_visible())
                page.get_by_role('button', name='Optional portal feedback').click()
                self.assertTrue(page.locator('#bo-feedback').is_visible())
                page.get_by_role('button', name='Close feedback', exact=True).click()
                self.assertFalse(page.locator('#bo-feedback').is_visible())
                nested = page.frame_locator('iframe.bo-workspace').frame_locator('iframe')
                self.assertTrue(nested.get_by_text('Material catalogue', exact=True).is_visible())
                with page.expect_popup() as popup_info:
                    nested.get_by_role('button', name='Open material / logistics helper window').click()
                popup = popup_info.value
                popup.wait_for_load_state()
                self.assertTrue(popup.get_by_role('heading', name='MAT-X material details').is_visible())
                self.assertTrue(page.locator('iframe.bo-workspace').is_visible())
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
