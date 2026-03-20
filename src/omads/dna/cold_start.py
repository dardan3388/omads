"""Cold-Start-Protokoll — Phasenverwaltung."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path


class OperationalPhase(str, Enum):
    manual_seed = "manual_seed"
    supervised_autonomy = "supervised_autonomy"
    full_operation = "full_operation"


def get_current_phase(dna_dir: Path) -> OperationalPhase:
    """Liest die aktuelle Betriebsphase aus cold_start_state.json."""
    path = dna_dir / "cold_start_state.json"
    if path.exists():
        try:
            state = json.loads(path.read_text())
            return OperationalPhase(state.get("current_phase", "manual_seed"))
        except (json.JSONDecodeError, OSError, ValueError):
            pass
    return OperationalPhase.manual_seed
