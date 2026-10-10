"""Interactive scouting helper (dev only, never part of the package).

usage: python dev/scout/act.py <siteId> <path> '<async python body using pg, ctx>'   (body '-' = read stdin)
Keeps a storage state per site in dev/scout/state/, logs in if redirected to /login, prints the aria snapshot.
Env: NOSNAP=1 (no snapshot), SHOT=file.png (full-page screenshot), MAX=chars.
"""
import asyncio, os, sys, textwrap
from playwright.async_api import async_playwright
sys.path.insert(0, os.path.dirname(__file__))
from _access import access

site, path, body = sys.argv[1], sys.argv[2], sys.argv[3]
code = sys.stdin.read() if body == "-" else body
ACC = access()[site]
STATE = os.path.join(os.path.dirname(__file__), "state", f"{site}.json")

async def main():
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=os.environ.get("HEADLESS", "1") == "1")
        ctx = await b.new_context(viewport={"width": 1280, "height": 800}, accept_downloads=True,
                                  storage_state=STATE if os.path.exists(STATE) else None)
        pg = await ctx.new_page()
        pg.on("dialog", lambda d: (print("DIALOG:", d.type, d.message), asyncio.ensure_future(d.dismiss())))
        await pg.goto(ACC["url"] + path, wait_until="networkidle")
        if "/login" in pg.url:
            c = ACC["credentials"]
            await pg.get_by_label("Benutzername").fill(c["username"])
            await pg.get_by_label("Passwort").fill(c["password"])
            await pg.get_by_role("button", name="Anmelden").click()
            await pg.wait_for_load_state("networkidle")
            print("(logged in)")
            if path:
                await pg.goto(ACC["url"] + path, wait_until="networkidle")
        ns = {"pg": pg, "ctx": ctx, "asyncio": asyncio}
        exec("async def __f():\n" + textwrap.indent(code or "pass", "    "), ns)
        await ns["__f"]()
        await pg.wait_for_timeout(500)
        await ctx.storage_state(path=STATE)
        print("URL:", pg.url)
        for f in pg.frames[1:]:
            print("FRAME:", f.url)
        if os.environ.get("NOSNAP") != "1":
            print((await pg.locator("body").aria_snapshot())[: int(os.environ.get("MAX", "12000"))])
        if os.environ.get("SHOT"):
            await pg.screenshot(path=os.environ["SHOT"], full_page=True)
        await b.close()

asyncio.run(main())
