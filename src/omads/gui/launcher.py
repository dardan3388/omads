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

    from .state import _get_setting

    # When LAN access is enabled and no explicit --host was given, bind to
    # all interfaces so phones on the same network can reach the GUI.
    if host == "127.0.0.1" and _get_setting("lan_access", False):
        host = "0.0.0.0"

    os.environ["OMADS_PORT"] = str(port)

    from .app import app

    local_url = f"http://localhost:{port}"
    url = f"http://{host}:{port}"
    print(f"\n  OMADS GUI starting on {url} ...")
    if host == "0.0.0.0":
        from .state import _detect_lan_ip
        lan_ip = _detect_lan_ip()
        print(f"  LAN access enabled — open http://{lan_ip}:{port} on your phone")

    def open_browser_when_ready():
        """Wait until the server responds, then open the browser."""
        check_url = local_url if host == "0.0.0.0" else url
        for _ in range(30):
            try:
                urllib.request.urlopen(check_url, timeout=1)
                print(f"  OMADS GUI: {check_url}\n")
                webbrowser.open(check_url)
                return
            except Exception:
                time.sleep(0.5)
        webbrowser.open(check_url)

    if should_open_browser(open_browser):
        threading.Thread(target=open_browser_when_ready, daemon=True).start()
    else:
        print("  Browser auto-open disabled.\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
