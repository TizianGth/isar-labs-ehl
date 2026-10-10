"""Fetch pages with a saved session (dev only). usage: python dev/scout/fetch.py <siteId> <outprefix> <path>...
Saves raw HTML to dev/scout/out/<prefix>_<i>.html and prints a rough text view (aria-labels inline)."""
import asyncio, html, os, re, sys
from playwright.async_api import async_playwright
sys.path.insert(0, os.path.dirname(__file__))
from _access import access

site, pre, paths = sys.argv[1], sys.argv[2], sys.argv[3:]
base = access()[site]["url"]
HERE = os.path.dirname(__file__)

def text(s: str) -> str:
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", s, flags=re.S)
    s = re.sub(r'aria-label="([^"]*)"', r"> [aria:\1] <x", s)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s)).replace("​", "")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", s))

async def main():
    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(storage_state=os.path.join(HERE, "state", f"{site}.json"))
        for i, path in enumerate(paths):
            r = await ctx.request.get(base + path)
            t = await r.text()
            fn = os.path.join(HERE, "out", f"{pre}_{i}.html")
            open(fn, "w").write(t)
            m = t.find("<main")
            print(f"##### {path} -> {r.status} ({fn})")
            print(text(t[max(m, 0):])[: int(os.environ.get("MAX", "5000"))])
        await b.close()

asyncio.run(main())
