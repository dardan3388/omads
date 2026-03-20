"""Projektpfad-Utilities."""

from __future__ import annotations

from pathlib import Path


def get_project_root() -> Path:
    """Gibt das OMADS-Projektverzeichnis zurück (wo pyproject.toml liegt)."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("Kein pyproject.toml gefunden — OMADS-Root nicht ermittelbar.")


def get_data_dir() -> Path:
    return get_project_root() / "data"


def get_dna_dir() -> Path:
    return get_project_root() / "dna"
