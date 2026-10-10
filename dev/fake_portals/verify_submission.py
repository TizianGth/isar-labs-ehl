"""Real browser form/signing checks using deliberately incorrect business values.

This tests the portal, not the agent's competence. No expected answer is posted.
"""
import asyncio
import json
import re
from pathlib import Path

from playwright.async_api import async_playwright
from server import PortalCluster
from verify_browser import login


async def verify():
    results = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        for profile in ('classic', 'modern', 'legacy'):
            with PortalCluster('baseline', profile) as cluster:
                tender_context = await browser.new_context()
                mail_context = await browser.new_context()
                tender = await tender_context.new_page(); mail = await mail_context.new_page()
                await login(tender, cluster.origins['tender'])
                await login(mail, cluster.origins['mail'])
                proposal_path = {'classic': '/angebot-einreichen', 'modern': '/proposal', 'legacy': '/portal/rfq_bid.html'}[profile]
                await tender.locator(f'a[href="{proposal_path}"]').first.click()
                values = {'bidder_company': 'Entered Test Company', 'bidder_vat': 'ENTERED-VAT',
                          'discount_percent': '3,5', 'total_net': '42,35'}
                for i in range(2):
                    for key, value in {'quantity': str(7+i), 'unit_price': '1,21', 'base_unit_price': '0,93',
                                       'surcharge_per_unit': '0,14', 'packaging_per_unit': '0,21',
                                       'rebate_total': '0', 'lead_time_weeks': '3'}.items():
                        values[f'pos_{i}_{key}'] = value
                    await tender.locator(f'[name="pos_{i}_material"]').select_option('SYN-MAT-BX')
                for key, value in values.items():
                    await tender.locator(f'[name="{key}"]').fill(value)
                await tender.locator('[name="accept_documents"]').check()
                await tender.locator('[name="own_terms"]').uncheck()
                await tender.locator('form button[type="submit"]').click()
                assert '42.35' in await tender.locator('body').inner_text()
                # Edit must restore what was entered rather than canned initial defaults.
                await tender.locator(f'a[href="{proposal_path}"]').last.click()
                assert await tender.locator('[name="total_net"]').input_value() == '42.35'
                assert await tender.locator('[name="pos_0_material"]').input_value() == 'SYN-MAT-BX'
                assert await tender.locator('[name="bidder_company"]').input_value() == 'Entered Test Company'
                await tender.locator('form button[type="submit"]').click()
                await tender.locator('form button[type="submit"]').click()  # Request signing TAN.
                await mail.goto(cluster.origins['mail'])
                await mail.get_by_role('link', name=re.compile('Signatur-TAN')).first.click()
                tan = re.search(r'TAN-\d{6}', await mail.locator('body').inner_text()).group()
                await tender.locator('[name="tan"]').fill(tan)
                async def dismiss(dialog): await dialog.dismiss()
                tender.once('dialog', dismiss)
                await tender.locator('form button[type="submit"]').click()
                assert cluster.snapshot()['submitted'] is None, 'Canceling confirmation still submitted'
                async def accept(dialog): await dialog.accept()
                tender.once('dialog', accept)
                await tender.locator('form button[type="submit"]').click()
                state = cluster.snapshot()
                assert state['receipt'] in await tender.locator('body').inner_text()
                assert state['submitted']['total_net'] == 42.35
                assert state['submitted']['discount_percent'] == 3.5
                assert state['submitted']['positions'][0]['unit_price'] == 1.21
                assert state['submitted']['positions'][0]['material_number'] == 'SYN-MAT-BX'
                assert state['tan_requests'] == 1
                results.append({'profile': profile, 'actual_form_readback': True, 'native_cancel': True,
                                'native_confirm': True, 'recorded_incorrect_values_unchanged': True})
                await tender_context.close(); await mail_context.close()
        await browser.close()
    Path(__file__).with_name('browser-smoke').joinpath('submission-report.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    asyncio.run(verify())
