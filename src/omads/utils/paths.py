"""Project path utilities."""

from __future__ import annotations

from pathlib import Path


def get_project_root() -> Path:
    """Return the OMADS project root (where `pyproject.toml` lives)."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("No pyproject.toml found; could not determine the OMADS root.")


def get_data_dir() -> Path:
    return get_project_root() / "data"


def get_dna_dir() -> Path:
    return get_project_root() / "dna"
