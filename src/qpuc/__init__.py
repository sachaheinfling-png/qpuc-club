from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def main() -> None:
    """Lance l'application QPUC depuis le script principal du dépôt."""
    script = Path(__file__).resolve().parents[2] / "qpuc.py"
    if not script.exists():
        raise FileNotFoundError(f"Script QPUC introuvable : {script}")

    spec = importlib.util.spec_from_file_location("qpuc_app", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Impossible de charger le script QPUC : {script}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    if hasattr(module, "main"):
        module.main()
        return

    raise AttributeError("Le script QPUC n'expose pas de fonction main()")


__all__ = ["main"]
