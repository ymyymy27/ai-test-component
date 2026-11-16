"""Hand-written L3 Playwright script used by the 2026-11-16 G3b AC12 recheck.

The worker writes this file to ``runs/<run_id>/generated/shot.py`` and runs it
with the project root as the working directory. It serves the project's static
page on a temporary HTTP port, opens it in Chromium and takes two screenshots:
one under ``test-results/`` (default Playwright output location) and one under
``ui/`` (an explicit ``page.screenshot(path=...)`` target).
"""

import http.server
import pathlib
import threading

from playwright.sync_api import sync_playwright

root = pathlib.Path.cwd()
handler = lambda *args, **kwargs: http.server.SimpleHTTPRequestHandler(  # noqa: E731
    *args, directory=str(root), **kwargs
)
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{server.server_address[1]}/index.html"

try:
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page(viewport={"width": 900, "height": 600})
        page.goto(url)
        page.wait_for_selector("h1")
        pathlib.Path("test-results").mkdir(exist_ok=True)
        page.screenshot(path="test-results/home.png")
        pathlib.Path("ui").mkdir(exist_ok=True)
        page.screenshot(path="ui/detail.png")
        browser.close()
finally:
    server.server_close()
