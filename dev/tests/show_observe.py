"""Print the serializer output for a fixture: uv run --project agent python dev/tests/show_observe.py <fixture.html>"""
import asyncio, sys
from pathlib import Path
from playwright.async_api import async_playwright
JS = (Path(__file__).parents[2] / "agent/rfq/browser/observe.js").read_text()
async def main(path):
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page()
        await pg.set_content(Path(path).read_text(), wait_until="domcontentloaded")
        await pg.wait_for_timeout(1300)
        r = await pg.evaluate(JS, {"prefix": "e"})
        print(r["text"]); print("--- refs:", r["refs"], "tz:", r["timezone"])
        await b.close()
asyncio.run(main(sys.argv[1]))
