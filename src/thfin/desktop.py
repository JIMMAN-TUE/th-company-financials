"""Desktop launcher, shared by thfin app and the self-contained Windows executable."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

from .app import AppController, default_workspace, prepare_workspace
from .uiserver import ScopeUI, make_server


def configure_bundled_browser():
    if getattr(sys, "frozen", False):
        bundled = Path(sys._MEIPASS) / "browsers"
        if bundled.is_dir():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled)


def serve_app(workspace: Path | None = None, port: int = 0, open_browser: bool = True) -> int:
    configure_bundled_browser()
    root = (workspace or default_workspace()).resolve()
    ui = ScopeUI(prepare_workspace(root))
    ui.app = AppController(ui, root)
    server = make_server(ui, port)
    url = f"http://127.0.0.1:{ui.port}/"
    print(f"thfin app: {url}", flush=True)
    print(f"Workspace: {root}\nKeep this window open while using the app. Ctrl+C to stop.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ui.app.shutdown()
        server.server_close()
    return 0


def self_test(executable_path: str | None = None) -> int:
    """Exercise frozen UI + bundled Chromium on loopback; no external website or user data."""
    from playwright.sync_api import sync_playwright

    configure_bundled_browser()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ui = ScopeUI(prepare_workspace(root))
        ui.app = AppController(ui, root)
        server = make_server(ui, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path=executable_path)
                try:
                    page = browser.new_page()
                    page.goto(f"http://127.0.0.1:{ui.port}/")
                    page.wait_for_selector("#collectJob")
                    page.wait_for_function("() => document.querySelector('#workspacePath').textContent.length > 0")
                    assert "thfin" in page.title()
                    with page.expect_response(lambda r: r.url.endswith("/api/upload")):
                        page.locator("#registryUpload").set_input_files({
                            "name": "fictional.csv", "mimeType": "text/csv",
                            "buffer": ("JURISTIC_ID,JURISTIC_NAME_TH,PROVINCE,TSIC_CODE\n"
                                       "0105500000001,Fictional company,สมุทรปราการ,10111\n").encode("utf-8")})
                    # Explicit IDs ignore missing type/status columns in this tiny fictional fixture.
                    page.locator("#incl").fill("0105500000001")
                    page.wait_for_function("() => document.querySelector('#kSelected').textContent === '1'")
                    page.locator("summary").filter(has_text="ใช้ไฟล์ที่ export ด้วยมือ").click()
                    page.locator("#queueJob").click()
                    page.wait_for_function("() => document.querySelector('#jobStatus').textContent.includes('งานเสร็จแล้ว')")
                    with page.expect_download() as download:
                        page.locator("#openQueue").click()
                    assert download.value.suggested_filename == "manual-queue.html"
                    print("Frozen app self-test passed: scope widget, upload API, UI assets and bundled Chromium", flush=True)
                finally:
                    browser.close()
        finally:
            ui.app.shutdown()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="thfin local application")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--self-test", action="store_true", help="verify the frozen app against loopback fixtures")
    args = parser.parse_args()
    try:
        return self_test() if args.self_test else serve_app(args.workspace, args.port, not args.no_browser)
    except Exception as e:
        print(f"thfin could not start: {e}", file=sys.stderr)
        return 1
