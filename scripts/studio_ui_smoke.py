"""Browser smoke test of the Studio editor (needs: pip install playwright; a running
`ultraai studio --no-window` on :8766 with a library holding an Agnes + a Queen song).
The browser path below is the Playwright-cached Chromium on the dev server; adjust it."""
import sys, time, json, urllib.request
from playwright.sync_api import sync_playwright
S = sys.argv[1]; errs = []
def doc(sid): return json.load(urllib.request.urlopen(f"http://127.0.0.1:8766/api/songs/{sid}/doc"))["doc"]
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/home/ubuntu/.cache/ms-playwright/chromium-1234/chrome-linux/chrome",
                          args=["--autoplay-policy=no-user-gesture-required"])
    pg = b.new_page(viewport={"width": 1280, "height": 720})
    pg.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    pg.goto("http://127.0.0.1:8766/"); pg.wait_for_selector(".song")
    songs = json.load(urllib.request.urlopen("http://127.0.0.1:8766/api/songs"))
    agnes = next(s for s in songs if s["artist"] == "Agnes")["id"]
    queen = next(s for s in songs if s["artist"] == "Queen")["id"]
    # --- tap mode on line 3
    pg.goto(f"http://127.0.0.1:8766/#/song/{agnes}"); pg.wait_for_selector(".timeline canvas"); time.sleep(1)
    pg.get_by_role("tab", name="Lines").click()
    pg.locator(".panel:not([hidden]) .item").nth(2).click(); time.sleep(0.3)
    before = doc(agnes)["voices"][0]["lines"][2]["notes"]
    pg.keyboard.press("t"); time.sleep(0.2)
    banner = pg.locator(".tapbanner").inner_text()
    n = len(before)
    t0 = time.time()
    time.sleep(2.0)  # lead-in
    for k in range(n):
        pg.keyboard.down("j"); time.sleep(0.25); pg.keyboard.up("j"); time.sleep(0.15)
    time.sleep(2.5)
    after = doc(agnes)["voices"][0]["lines"][2]["notes"]
    print("tap:", banner[:40], "| starts before", [round(x["start"],2) for x in before], "after", [round(x["start"],2) for x in after])
    print("tap gaps ok:", all(abs((after[i+1]["start"]-after[i]["start"]) - 0.4) < 0.15 for i in range(n-1)))
    # --- undo restores
    pg.keyboard.press("Control+z"); time.sleep(2.0)
    print("undo restores:", [round(x["start"],2) for x in doc(agnes)["voices"][0]["lines"][2]["notes"]] == [round(x["start"],2) for x in before])
    # --- drag a break marker
    pg.screenshot(path=f"{S}/ed-720.png")
    # --- imported song without vocals/editor data
    pg.goto(f"http://127.0.0.1:8766/#/song/{queen}"); pg.wait_for_selector(".timeline canvas"); time.sleep(1.5)
    pg.screenshot(path=f"{S}/ed-queen.png")
    print("queen vocals button disabled:", pg.get_by_role("button", name="Hear only the separated vocals (V)").is_disabled())
    b.close()
print("errors:", errs)
