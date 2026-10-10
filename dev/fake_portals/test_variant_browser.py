"""Local Chromium integration checks for the three difficult portal variants.

These tests use actual browser links/forms and deliberately incorrect invented
bid values. They verify the portal environment, not an LLM's quality score.
Run with the existing Playwright-enabled Python; no dependency installation.
"""
import json
import os
from pathlib import Path
import re
import time
import unittest
from urllib.parse import urlparse

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    sync_playwright = None

HERE = Path(__file__).resolve().parent
REPORT = HERE / 'variant-browser-report.json'


@unittest.skipIf(sync_playwright is None, 'Use an existing Playwright-enabled Python environment')
class VariantBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from variant_server import PortalCluster
        except ImportError as error:
            raise unittest.SkipTest('variant_server integration module is not yet available') from error
        cls.cluster_class = PortalCluster
        cache = HERE.parents[1] / 'work' / 'atira-browsers'
        if cache.exists():
            os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(cache))
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception:
            cls.playwright.stop()
            raise
        cls.results = []

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        REPORT.write_text(json.dumps({'kind': 'deterministic-local-browser-integration',
            'not_an_agent_quality_score': True, 'results': cls.results}, indent=2) + '\n')

    def context(self):
        context = self.browser.new_context(viewport={'width': 1280, 'height': 900})
        context.set_default_timeout(10000)
        self.errors = getattr(self, 'errors', [])
        self.off_origin = getattr(self, 'off_origin', [])
        def guard(route):
            if urlparse(route.request.url).hostname == '127.0.0.1':
                route.continue_()
            else:
                self.off_origin.append(route.request.url)
                route.abort()
        context.route('**/*', guard)
        context.on('page', lambda page: page.on('pageerror', lambda error: self.errors.append(str(error))))
        self.addCleanup(context.close)
        return context

    def login(self, page, origin):
        page.goto(origin)
        page.locator('input[name="username"]').fill('demo')
        page.locator('input[name="password"]').fill('synthetic-only')
        page.locator('form button[type="submit"],form input[type="submit"]').first.click()
        expect(page.locator('input[type="password"]')).to_have_count(0)

    def content_frame(self, page, selector, nested=False):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for frame in reversed(page.frames):
                if (not nested or frame.parent_frame is not None and frame.parent_frame.parent_frame is not None):
                    if frame.locator(selector).count():
                        return frame
            page.wait_for_timeout(50)
        self.fail(f'No expected content frame for {selector}: {[f.url for f in page.frames]}')

    def observed_link(self, scope, pattern):
        link = scope.get_by_role('link', name=re.compile(pattern)).first
        expect(link).to_be_visible()
        href = link.get_attribute('href')
        self.assertIsNotNone(href)
        self.assertTrue(href.startswith('/ui/'), f'Link did not use the variant route: {href}')
        return link

    def check_scanned_canvas(self, frame):
        canvas = frame.locator('canvas')
        expect(canvas).to_have_count(1)
        expect(canvas).to_be_visible()
        pixels = canvas.evaluate('''c => ({width:c.width,height:c.height,
            visibleWidth:c.getBoundingClientRect().width,
            painted:c.getContext('2d').getImageData(30,30,500,100).data.some((n,i)=>i%4!==3&&n<100)})''')
        self.assertTrue(pixels['painted'], pixels)
        self.assertGreaterEqual(pixels['visibleWidth'], 850)
        self.assertGreater(pixels['height'], 250)
        self.assertEqual(canvas.get_attribute('aria-label'), 'Scanned document attachment')
        return pixels

    def verify_profile(self, profile):
        started = time.monotonic()
        record = {'profile': profile, 'ok': False}
        self.results.append(record)
        self.errors, self.off_origin = [], []
        with self.cluster_class(case_name='baseline', profile=profile) as cluster:
            contexts = {role: self.context() for role in cluster.origins}
            pages = {role: context.new_page() for role, context in contexts.items()}
            for role, page in pages.items():
                self.login(page, cluster.origins[role])
                self.assertIn(f'/ui/{profile}/{role}/', page.url)
            tender, spec, erp, mail = (pages[r] for r in ('tender', 'specification', 'erp', 'mail'))
            record['authenticated_roles'] = list(pages)

            nested = profile == 'busy-office'
            spec_frame = self.content_frame(spec, 'a', nested=nested)
            if nested:
                self.assertGreaterEqual(len(spec.frames), 3)
                record['specification_nested_frames'] = len(spec.frames)
                self.observed_link(spec_frame, '^REQ-A$').click()
            else:
                self.observed_link(spec_frame, '^Requirement A$').click()
            expect(spec_frame.get_by_role('heading', name=re.compile('REV-C'))).to_be_visible()
            if profile == 'scanned':
                record['requirement_canvas'] = self.check_scanned_canvas(spec_frame)
                self.assertNotIn('120 mm', spec_frame.locator('body').inner_text())
            else:
                self.assertIn('120 mm', spec_frame.locator('body').inner_text())

            erp_frame = self.content_frame(erp, 'input[name="group"]', nested=nested)
            if nested:
                self.assertGreaterEqual(len(erp.frames), 3)
                record['erp_nested_frames'] = len(erp.frames)
            erp_frame.locator('input[name="group"]').fill('CAT-A')
            erp_frame.locator('form button[type="submit"]').click()
            expect(erp_frame.get_by_role('link', name='SYN-MAT-AX', exact=True)).to_be_visible()
            if profile == 'busy-office':
                button = erp_frame.get_by_role('button', name='Open material / logistics helper window').first
                expect(button).to_be_visible()
                with erp.expect_popup() as popup_result:
                    button.click()
                popup = popup_result.value
                popup.wait_for_load_state('domcontentloaded')
                self.assertIn('SYN-MAT-AX', popup.locator('body').inner_text())
                self.assertIn(f'/ui/{profile}/erp/', popup.url)
                record['actual_material_popup'] = True
                popup.close()
            self.observed_link(erp_frame, '^SYN-MAT-AX$').click()
            expect(erp_frame.get_by_role('heading', name=re.compile('SYN-MAT-AX'), level=2)).to_be_visible()
            if profile == 'workbench':
                disclosure = erp_frame.locator('details.workbench-material')
                self.assertFalse(disclosure.evaluate('(e)=>e.open'))
                disclosure.locator('summary').click()
                erp_frame.get_by_role('tab').nth(2).click()
                expect(erp_frame.locator('[role=tabpanel]').nth(2)).to_be_visible()
                self.assertIn('90,00', erp_frame.locator('[role=tabpanel]').nth(2).inner_text())
                record['material_disclosure_and_tabs'] = True
            elif profile == 'scanned':
                erp_frame.get_by_role('tab').nth(2).click()
                record['condition_canvas'] = self.check_scanned_canvas(erp_frame)
                self.assertNotIn('90,00', erp_frame.locator('body').inner_text())

            self.observed_link(tender, '^(Q&A|FAQ & QA)$').click()
            if profile == 'workbench':
                rows = tender.locator('[data-workbench-qa] tbody tr')
                expect(rows).to_have_count(2)
                more = tender.locator('[data-workbench-loadmore]')
                requests = []
                tender.on('request', lambda request: requests.append(request.url) if request.resource_type == 'fetch' else None)
                more.click()
                expect(rows).to_have_count(4)
                more.click()
                expect(rows).to_have_count(len(cluster.case['qa']))
                expect(more).to_have_count(0)
                self.assertTrue(any('page=2' in url for url in requests), requests)
                self.assertTrue(any('page=3' in url for url in requests), requests)
                record['qa_real_load_more_requests'] = list(requests)
                self.assertIn('CLAUSE-A', tender.locator('body').inner_text())
            else:
                self.assertIn('Antwort #99' if nested else 'Answer #99', tender.locator('body').inner_text())

            self.observed_link(mail, '^(Archive|Archive Folder)$').click()
            self.observed_link(mail, 'FX fixing').click()
            expect(mail.locator('body')).to_contain_text('0.80')
            record['archive_navigation'] = True
            self.assertIsNone(cluster.snapshot()['submitted'], 'Read-only actions submitted a proposal')
            self.verify_submission(cluster, tender, mail, profile)
            self.assertFalse(self.errors, self.errors)
            self.assertFalse(self.off_origin, self.off_origin)
            record.update(ok=True, browser_js_errors=[], external_requests=[], elapsed_seconds=round(time.monotonic()-started,3))

    def verify_submission(self, cluster, tender, mail, profile):
        proposal = self.observed_link(tender, '^(Submit Proposal|Proposal Entry)$')
        proposal_href = proposal.get_attribute('href')
        proposal.click()
        expect(tender.locator('[name="total_net"]')).to_be_visible()
        values = {'bidder_company': 'Entered Variant Test Company', 'bidder_vat': 'ENTERED-VARIANT-VAT',
                  'discount_percent': '3,5', 'total_net': '42,35'}
        for index in range(2):
            for key, value in {'quantity': str(7+index), 'unit_price': '1,21', 'base_unit_price': '0,93',
                              'surcharge_per_unit': '0,14', 'packaging_per_unit': '0,21',
                              'rebate_total': '0', 'lead_time_weeks': '3'}.items():
                values[f'pos_{index}_{key}'] = value
            tender.locator(f'[name="pos_{index}_material"]').select_option('SYN-MAT-BX')
        for name, value in values.items():
            tender.locator(f'[name="{name}"]').fill(value)
        tender.locator('[name="accept_documents"]').check()
        tender.locator('[name="own_terms"]').uncheck()
        tender.locator('form button[type="submit"]').click()
        expect(tender.locator('body')).to_contain_text('42.35')
        tender.locator(f'a[href="{proposal_href}"]').last.click()
        expect(tender.locator('[name="total_net"]')).to_have_value('42.35')
        expect(tender.locator('[name="pos_0_material"]')).to_have_value('SYN-MAT-BX')
        tender.locator('form button[type="submit"]').click()
        tender.locator('form button[type="submit"]').click()
        expect(tender.locator('[name="tan"]')).to_be_visible()
        mail.goto(cluster.origins['mail'])
        self.observed_link(mail, 'Signatur-TAN').click()
        expect(mail.locator('body')).to_contain_text(cluster.case['reference'])
        match = re.search(r'TAN-\d{6}', mail.locator('body').inner_text())
        self.assertIsNotNone(match)
        tender.locator('[name="tan"]').fill(match.group())
        tender.once('dialog', lambda dialog: dialog.dismiss())
        tender.locator('form button[type="submit"]').click()
        self.assertIsNone(cluster.snapshot()['submitted'], 'Dismissed confirmation still submitted')
        tender.once('dialog', lambda dialog: dialog.accept())
        tender.locator('form button[type="submit"]').click()
        state = cluster.snapshot()
        self.assertIsNotNone(state['receipt'])
        expect(tender.locator('body')).to_contain_text(state['receipt'])
        self.assertEqual(state['submitted']['total_net'], 42.35)
        self.assertEqual(state['submitted']['discount_percent'], 3.5)
        self.assertEqual(state['submitted']['positions'][0]['unit_price'], 1.21)
        self.assertEqual(state['submitted']['positions'][0]['material_number'], 'SYN-MAT-BX')
        self.assertEqual(state['tan_requests'], 1)
        self.results[-1]['submission'] = {'edit_restores_entered_values': True, 'tan_read_from_mail': True,
            'native_cancel_prevents_submit': True, 'native_confirm_creates_receipt': True,
            'incorrect_business_values_preserved': True}

    def test_workbench(self):
        self.verify_profile('workbench')

    def test_scanned(self):
        self.verify_profile('scanned')

    def test_busy_office(self):
        self.verify_profile('busy-office')


if __name__ == '__main__':
    unittest.main(verbosity=2)
