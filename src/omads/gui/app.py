"""FastAPI app and middleware for the OMADS GUI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .routes import router as routes_router
from .state import _get_setting
from .websocket import router as websocket_router

app = FastAPI(title="OMADS GUI")

# When LAN access is enabled the CORS regex also covers RFC-1918 private IPs
# so mobile browsers on the same network can reach the API and WebSocket.
_lan_enabled = _get_setting("lan_access", False)

_LOCAL_ORIGIN_RE = r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$"
_LAN_ORIGIN_RE = (
    r"^https?://(127\.0\.0\.1|localhost"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3})(:\d+)?$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1", "http://localhost"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
    allow_origin_regex=_LAN_ORIGIN_RE if _lan_enabled else _LOCAL_ORIGIN_RE,
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    csp = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:"
    if _lan_enabled:
        csp += "; connect-src 'self' ws: wss:"
    response.headers["Content-Security-Policy"] = csp
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(routes_router)
app.include_router(websocket_router)
