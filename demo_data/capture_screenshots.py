"""
Captures the README screenshots from the frontend-only demo (?demo=1).
Serves app/ on a free loopback port, drives one Indomie Session from setup
to results in headless Chromium, and writes PNGs to docs/media/.

Run: python demo_data/capture_screenshots.py
Fails on any page error or missing screen, so a UI change cannot leave
stale images behind silently.
"""

import csv
import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
OUT_DIR = ROOT / "docs" / "media"
VIDEO_IDS = [row["video_id"] for row in csv.DictReader(open(ROOT / "demo_data" / "videos.csv", encoding="utf-8"))]
TIMEOUT_MS = 60_000


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve_app():
    handler = functools.partial(QuietHandler, directory=str(APP_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def capture(base):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=2)
        page.set_default_timeout(TIMEOUT_MS)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(base + "/?demo=1#/sessions/new")
        page.fill("input#f-session-name", "Indomie Cabe Ijo")
        page.locator("input#f-session-name").blur()
        page.wait_for_function("/^#\\/sessions\\/[^/]+\\/campaigns\\//.test(location.hash)")
        for i, video_id in enumerate(VIDEO_IDS, 1):
            page.fill("input#c-url", "https://www.youtube.com/watch?v=" + video_id)
            page.click("#c-add-url")
            page.wait_for_function(f"document.querySelectorAll('.video-row').length >= {i}")
        page.click("#btn-create-session")
        page.click("#btn-run")
        # Start can stack two confirms: stale Key Messages and overwrite.
        for _ in range(2):
            try:
                page.click("#overlay-root [data-confirm-continue]", timeout=1500)
            except Exception:
                break

        page.wait_for_function("/^#\\/runs\\/[^/]+$/.test(location.hash)")
        page.wait_for_timeout(2500)
        page.screenshot(path=OUT_DIR / "run-progress.png")

        confirm = page.get_by_role("button", name="Confirm and continue")
        confirm.wait_for()
        page.wait_for_timeout(500)
        page.screenshot(path=OUT_DIR / "key-message-review.png")
        confirm.click()

        page.get_by_text("Open results").click()
        page.wait_for_function("/\\/results$/.test(location.hash)")
        page.locator("[data-metric]").first.wait_for()
        page.wait_for_timeout(1000)
        page.screenshot(path=OUT_DIR / "results.png")

        # The first Key Message travel bar opens the comments behind it.
        page.locator(".bar-val[data-metric]").first.click()
        panel = page.locator(".ev-panel").first
        panel.wait_for()
        page.wait_for_timeout(800)
        # Put the section heading just under the sticky top bar.
        page.get_by_role("heading", name="Key Message travel").evaluate(
            "h => window.scrollTo(0, h.getBoundingClientRect().top + window.scrollY - 110)")
        page.wait_for_timeout(300)
        page.screenshot(path=OUT_DIR / "evidence.png")
        browser.close()

    assert not errors, f"page errors during capture: {errors}"


if __name__ == "__main__":
    server = serve_app()
    try:
        capture(f"http://127.0.0.1:{server.server_address[1]}")
    finally:
        server.shutdown()
    for png in sorted(OUT_DIR.glob("*.png")):
        print(f"{png.relative_to(ROOT)}  {png.stat().st_size // 1024} KB")
