"""Startup helpers for the GUI server."""

from __future__ import annotations

import os


def should_open_browser(open_browser: bool = True) -> bool:
    """Return whether OMADS should try to open a local browser window."""
    if not open_browser:
        return False
    raw = os.environ.get("OMADS_OPEN_BROWSER", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def start_gui(host: str = "127.0.0.1", port: int = 8080, open_browser: bool = True):
    """Start the GUI server."""
    import threading
    import time
    import urllib.request
    import webbrowser

    import uvicorn

    from .app import app

    url = f"http://{host}:{port}"
    print(f"\n  OMADS GUI starting on {url} ...")

    def open_browser_when_ready():
        """Wait until the server responds, then open the browser."""
        for _ in range(30):
            try:
                urllib.request.urlopen(url, timeout=1)
                print(f"  OMADS GUI: {url}\n")
                webbrowser.open(url)
                return
            except Exception:
                time.sleep(0.5)
        webbrowser.open(url)

    if should_open_browser(open_browser):
        threading.Thread(target=open_browser_when_ready, daemon=True).start()
    else:
        print("  Browser auto-open disabled.\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
