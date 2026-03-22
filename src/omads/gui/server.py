"""Kompatibilitäts-Fassade für die modulare OMADS-GUI."""

from __future__ import annotations

from .app import app
from .launcher import start_gui

__all__ = ["app", "start_gui"]
