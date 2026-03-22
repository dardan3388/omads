"""Start-Helfer für den GUI-Server."""

from __future__ import annotations


def start_gui(host: str = "127.0.0.1", port: int = 8080):
    """Startet den GUI-Server."""
    import threading
    import time
    import urllib.request
    import webbrowser

    import uvicorn

    from .app import app

    url = f"http://{host}:{port}"
    print(f"\n  OMADS GUI startet auf {url} ...")

    def open_browser_when_ready():
        """Wartet bis der Server antwortet, dann öffnet den Browser."""
        for _ in range(30):
            try:
                urllib.request.urlopen(url, timeout=1)
                print(f"  OMADS GUI: {url}\n")
                webbrowser.open(url)
                return
            except Exception:
                time.sleep(0.5)
        webbrowser.open(url)

    threading.Thread(target=open_browser_when_ready, daemon=True).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
