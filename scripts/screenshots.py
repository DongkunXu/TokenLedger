"""Take the README screenshots of the running dashboard, in English and Chinese.

    scripts/dashboard.sh start
    source scripts/env.sh   # from bash, keeps uv's cache inside the project
    uv run --no-project --with playwright scripts/screenshots.py [url]

Uses the installed Google Chrome (no browser download). Writes to docs/images/:
dashboard-<lang>.jpg (the whole page, dark theme) and equivalents-<lang>.jpg (the
fun equivalents dialog). The screenshots show whatever is in the ledger.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "images"
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/"
VIEWPORT = {"width": 1440, "height": 900}
SETTLE_MS = 2500  # entry animations and number scrambles


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        for lang in ("en", "zh"):
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2, color_scheme="dark")
            page.add_init_script(
                f"localStorage.setItem('tokenledger.lang', '{lang}');"
                "localStorage.setItem('tokenledger.theme', 'dark');"
            )
            page.goto(URL, wait_until="networkidle")
            page.wait_for_timeout(SETTLE_MS)
            # A window as tall as the page, so the fixed background and rail cover all of it.
            height = page.evaluate("document.documentElement.scrollHeight")
            page.set_viewport_size({"width": VIEWPORT["width"], "height": height})
            page.wait_for_timeout(SETTLE_MS)
            page.screenshot(path=OUT / f"dashboard-{lang}.jpg", type="jpeg", quality=85)

            # Tall enough that the dialog shows every group without scrolling.
            page.set_viewport_size({"width": VIEWPORT["width"], "height": 1600})
            page.click(".fun-line")
            page.wait_for_timeout(SETTLE_MS)
            page.locator(".fun-dialog").screenshot(path=OUT / f"equivalents-{lang}.jpg", type="jpeg", quality=85)
            page.close()
        browser.close()
    for f in sorted(OUT.glob("*.jpg")):
        print(f"{f.relative_to(OUT.parent.parent)}  {f.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
