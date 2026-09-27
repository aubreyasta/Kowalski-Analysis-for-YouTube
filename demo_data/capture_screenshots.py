"""
Captures the README screenshots and walkthrough GIF from the frontend-only
demo (?demo=1). Serves app/ on a free loopback port, drives one Indomie
Session from Home to results in headless Chromium, and writes to docs/media/.

Run: python demo_data/capture_screenshots.py   (needs ffmpeg on PATH)
Fails on any page error or missing screen, so a UI change cannot leave
stale media behind silently.
"""

import csv
import functools
import shutil
import subprocess
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
OUT_DIR = ROOT / "docs" / "media"
VIDEO_IDS = [row["video_id"] for row in csv.DictReader(open(ROOT / "demo_data" / "videos.csv", encoding="utf-8"))]
TIMEOUT_MS = 60_000
GIF_WIDTH = 960

# Headless screenshots have no mouse pointer, so the GIF draws its own.
CURSOR_JS = """() => {
  const c = document.createElement('div');
  c.id = 'rec-cursor';
  c.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24"><path d="M4 2l16 10-7 1.6L9.4 21z" '
    + 'fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  c.style.cssText = 'position:fixed;left:0;top:0;z-index:2147483647;pointer-events:none';
  document.body.appendChild(c);
}"""


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve_app():
    handler = functools.partial(QuietHandler, directory=str(APP_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class Recorder:
    """Screenshots the page as GIF frames, each with its own display time,
    so slow waits can be time-lapsed and key screens held."""

    def __init__(self, page, frame_dir):
        self.page, self.dir = page, frame_dir
        self.frames = []  # (png path, seconds shown)
        self.x, self.y = 640, 560
        page.evaluate(CURSOR_JS)
        self._place(self.x, self.y)

    def _place(self, x, y):
        # The arrow's tip sits at (4, 2) inside its 24px box.
        self.page.evaluate(f"document.getElementById('rec-cursor').style.transform = 'translate({x - 4}px, {y - 2}px)'")

    def shot(self, ms):
        path = self.dir / f"{len(self.frames):04d}.png"
        self.page.screenshot(path=path)
        self.frames.append((path, ms / 1000))

    def still(self, name):
        cursor = "document.getElementById('rec-cursor').style.visibility"
        self.page.evaluate(f"{cursor} = 'hidden'")
        self.page.screenshot(path=OUT_DIR / name)
        self.page.evaluate(f"{cursor} = ''")

    def move_to(self, locator, steps=8):
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        tx, ty = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        for i in range(1, steps + 1):
            t = i / steps
            t = t * t * (3 - 2 * t)  # ease in and out
            self._place(self.x + (tx - self.x) * t, self.y + (ty - self.y) * t)
            self.shot(35)
        self.x, self.y = tx, ty
        self.shot(150)

    def click(self, locator, hold_ms=500):
        self.move_to(locator)
        locator.click()
        self.page.wait_for_timeout(250)
        self.shot(hold_ms)

    def type(self, locator, text):
        self.click(locator, hold_ms=150)
        for ch in text:
            self.page.keyboard.type(ch)
            self.shot(60)

    def time_lapse(self, done_js, every_ms=600, show_ms=220):
        for _ in range(TIMEOUT_MS // every_ms):
            self.shot(show_ms)
            if self.page.evaluate(done_js):
                return
            self.page.wait_for_timeout(every_ms)
        raise TimeoutError(f"never reached: {done_js}")

    def scroll_to(self, y, steps=10):
        start = self.page.evaluate("window.scrollY")
        for i in range(1, steps + 1):
            t = i / steps
            t = t * t * (3 - 2 * t)
            self.page.evaluate(f"window.scrollTo(0, {start + (y - start) * t})")
            self.shot(40)

    def write_gif(self, out):
        listing = self.dir / "frames.txt"
        lines = [f"file '{p.name}'\nduration {s:.3f}" for p, s in self.frames]
        # The concat demuxer ignores the last duration unless the file repeats.
        lines.append(f"file '{self.frames[-1][0].name}'")
        listing.write_text("\n".join(lines), encoding="utf-8")
        palette = (f"scale={GIF_WIDTH}:-1:flags=lanczos,split[a][b];"
                   "[a]palettegen=max_colors=128:stats_mode=diff[p];"
                   "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(listing), "-vf", palette, "-fps_mode", "vfr", str(out)], check=True)


def capture(base, frame_dir):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=2)
        page.set_default_timeout(TIMEOUT_MS)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(base + "/?demo=1#/home")
        create = page.get_by_role("link", name="Create your first session")
        create.wait_for()
        rec = Recorder(page, frame_dir)
        rec.shot(1200)
        rec.click(create)

        name = page.locator("input#f-session-name")
        rec.type(name, "Indomie Cabe Ijo")
        name.blur()
        page.wait_for_function("/^#\\/sessions\\/[^/]+\\/campaigns\\//.test(location.hash)")
        for i, video_id in enumerate(VIDEO_IDS, 1):
            url_input = page.locator("input#c-url")
            rec.click(url_input, hold_ms=150)
            url_input.fill("https://www.youtube.com/watch?v=" + video_id)
            rec.shot(250)
            rec.click(page.locator("#c-add-url"))
            page.wait_for_function(f"document.querySelectorAll('.video-row').length >= {i}")
        rec.click(page.locator("#btn-create-session"))
        rec.click(page.locator("#btn-run"))
        # Start can stack two confirms: stale Key Messages and overwrite.
        for _ in range(2):
            try:
                page.locator("#overlay-root [data-confirm-continue]").first.wait_for(timeout=1500)
            except Exception:
                break
            rec.click(page.locator("#overlay-root [data-confirm-continue]").first)

        page.wait_for_function("/^#\\/runs\\/[^/]+$/.test(location.hash)")
        page.wait_for_timeout(2000)
        rec.still("run-progress.png")
        confirm = page.get_by_role("button", name="Confirm and continue")
        rec.time_lapse("document.getElementById('view').innerText.includes('Confirm and continue')")
        confirm.wait_for()
        page.wait_for_timeout(500)
        rec.still("key-message-review.png")
        rec.shot(1800)
        rec.click(confirm)

        open_results = page.get_by_text("Open results")
        rec.time_lapse("document.getElementById('view').innerText.includes('Open results')")
        rec.shot(800)
        rec.click(open_results)
        page.wait_for_function("/\\/results$/.test(location.hash)")
        page.locator("[data-metric]").first.wait_for()
        page.wait_for_timeout(1000)
        rec.still("results.png")
        rec.shot(2500)

        # Put the section heading just under the sticky top bar, then open
        # the comments behind the first Key Message's Travel figure.
        heading = page.get_by_role("heading", name="Key Message travel")
        rec.scroll_to(heading.evaluate("h => h.getBoundingClientRect().top + window.scrollY - 110"))
        rec.click(page.locator(".bar-val[data-metric]").first, hold_ms=300)
        page.locator(".ev-panel").first.wait_for()
        page.wait_for_timeout(800)
        rec.still("evidence.png")
        rec.shot(3500)
        browser.close()

    assert not errors, f"page errors during capture: {errors}"
    rec.write_gif(OUT_DIR / "walkthrough.gif")


if __name__ == "__main__":
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is not on PATH; it builds walkthrough.gif.")
    server = serve_app()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            capture(f"http://127.0.0.1:{server.server_address[1]}", Path(tmp))
    finally:
        server.shutdown()
    for f in sorted(OUT_DIR.iterdir()):
        print(f"{f.relative_to(ROOT)}  {f.stat().st_size // 1024} KB")
