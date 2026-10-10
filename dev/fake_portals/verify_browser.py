"""Independent Chromium smoke test, without an LLM or live challenge access."""
import asyncio
import json
import os
from collections import deque
from pathlib import Path
from urllib.parse import urlparse, urldefrag

from playwright.async_api import async_playwright
from server import PortalCluster

HERE = Path(__file__).resolve().parent


async def login(page, origin):
    await page.goto(origin)
    await page.locator('input[type="password"]').fill('synthetic-only')
    await page.locator('input:not([type="password"]):not([type="hidden"]):not([type="submit"])').first.fill('demo')
    await page.locator('form button, form input[type="submit"]').first.click()
    await page.wait_for_load_state('domcontentloaded')
    assert not await page.locator('input[type="password"]').count(), 'Login did not reach application'


async def crawl(page, origin, seeds=()):
    pending = deque([origin, *seeds]); seen = set(); bodies = []
    while pending and len(seen) < 45:
        url = urldefrag(pending.popleft())[0]
        if url in seen or urlparse(url).netloc != urlparse(origin).netloc:
            continue
        seen.add(url)
        response = await page.goto(url)
        assert response.status < 400, f'{url}: HTTP {response.status}'
        for frame in page.frames:
            bodies.append(await frame.locator('body').inner_text())
            for tab in await frame.get_by_role('tab').all():
                await tab.click()
                bodies.append(await frame.locator('body').inner_text())
            links = await frame.locator('a[href]').evaluate_all(
                '(els) => els.map(e => ({url:e.href, text:e.innerText}))')
            for link in links:
                if any(word in link['text'].lower() for word in ('logout', 'abmelden', 'sign out')):
                    continue
                if link['url'].startswith(origin):
                    pending.append(link['url'])
    return '\n'.join(bodies), seen


async def verify():
    result = []
    screenshots = HERE / 'browser-smoke'; screenshots.mkdir(exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        for profile in ('classic', 'modern', 'legacy'):
            with PortalCluster('baseline', profile) as cluster:
                for role, origin in cluster.origins.items():
                    context = await browser.new_context(viewport={'width': 1280, 'height': 900})
                    page = await context.new_page(); await login(page, origin)
                    await page.screenshot(path=str(screenshots / f'{profile}-{role}.png'), full_page=True)
                    if profile == 'legacy' and role in ('specification', 'erp'):
                        assert len(page.frames) >= 2, f'{role} has no actual frame'
                    seeds = []
                    if role == 'erp':
                        for group in ('CAT-A', 'CAT-B'):
                            await page.goto(origin)
                            frame = next((f for f in page.frames if f != page.main_frame), page.main_frame)
                            field = frame.locator('input:not([type="hidden"]):not([type="submit"])').first
                            await field.fill(group)
                            await frame.locator('form button, form input[type="submit"]').first.click()
                            await page.wait_for_timeout(100)
                            seeds.append(frame.url)
                    corpus, urls = await crawl(page, origin, seeds)
                    wanted = {'tender': ('SYN-RFQ-4401', 'X-01', 'X-02', 'withdrawn'),
                              'specification': ('REV-C', 'REV-A', 'REQ-A', 'REQ-B', 'CAT-A', 'CAT-B'),
                              'erp': ('SYN-MAT-AX', 'SYN-MAT-BX', 'SYN-MAT-DECOY', '1,100.00', 'INDEX-A'),
                              'mail': ('sales@boreal.example', 'FX fixing', 'Packaging tariffs', '80 percent')}[role]
                    for text in wanted:
                        assert text in corpus, f'{profile}/{role}: cannot reach {text}'
                    assert '1540.74' not in corpus, 'Expected answer leaked into page'
                    result.append({'profile': profile, 'role': role, 'visited': len(urls), 'ok': True})
                    await context.close()
                assert cluster.snapshot()['submitted'] is None, 'Read-only smoke changed submission'
        await browser.close()
    (screenshots / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    asyncio.run(verify())
