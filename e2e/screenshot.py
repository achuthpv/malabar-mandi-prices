#!/usr/bin/env python3
"""Capture dashboard screenshots (desktop/mobile, light/dark) for visual review.

Usage: python e2e/screenshot.py [output_dir]
Builds the site from the frozen synthetic dataset (same as the E2E suite).
"""

from __future__ import annotations

import socket
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from conftest import _seed_rows, REPO_ROOT  # noqa: E402


def build_site(root: Path) -> Path:
    import shutil

    sys.path.insert(0, str(REPO_ROOT / "pipeline" / "src"))
    from mandi.analyze import analyze_all
    from mandi.config import load_config
    from mandi.publish import publish_all
    from mandi.store import upsert_rows
    from conftest import TODAY, FETCHED_AT

    site = root / "site"
    site.mkdir(parents=True)
    for name in ("index.html", "openapi.json"):
        shutil.copy(REPO_ROOT / "site" / name, site / name)
    shutil.copytree(REPO_ROOT / "site" / "assets", site / "assets")
    data = root / "data"
    upsert_rows(_seed_rows(), base=data)
    cfg = load_config()
    analysis = analyze_all(cfg, base=data, today=TODAY)
    publish_all(cfg, generated_at=FETCHED_AT, api_dir=site / "api" / "v1",
                data_base=data, analysis=analysis)
    return site


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "screenshots")
    out.mkdir(parents=True, exist_ok=True)
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        site = build_site(Path(tmp))
        handler = partial(SimpleHTTPRequestHandler, directory=str(site))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{port}"

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            shots = [
                ("desktop-light", {"viewport": {"width": 1200, "height": 900}}, "light", "/#/arecanut"),
                ("mobile-light", {"viewport": {"width": 375, "height": 800}}, "light", "/#/black-pepper"),
                ("mobile-dark", {"viewport": {"width": 375, "height": 800}}, "dark", "/#/coconut"),
            ]
            for name, ctx_args, scheme, path in shots:
                ctx = browser.new_context(**ctx_args, color_scheme=scheme)
                page = ctx.new_page()
                page.goto(url + path)
                page.wait_for_selector("#main:not([hidden])")
                page.wait_for_timeout(600)
                page.screenshot(path=str(out / f"{name}.png"), full_page=True)
                ctx.close()
            browser.close()
        server.shutdown()
    print(f"screenshots written to {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
