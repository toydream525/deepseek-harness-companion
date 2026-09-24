"""Install and writable data paths for source and standalone Windows builds."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

INSTALL_ROOT = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parent.parent)


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix=".dsh-write-", dir=directory)
        os.close(handle)
        Path(name).unlink()
        return True
    except OSError:
        return False


if _writable(INSTALL_ROOT):
    DATA_ROOT = INSTALL_ROOT
else:
    app_data = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    DATA_ROOT = Path(app_data).expanduser() / "DSH-Companion" if app_data else Path.home() / "DSH-Companion"
    DATA_ROOT.mkdir(parents=True, exist_ok=True)

# ROOT is the historical writable-data name used by the library and downloads.
ROOT = DATA_ROOT
MANAGER = DATA_ROOT / "manager"
MODELS = (INSTALL_ROOT if _writable(INSTALL_ROOT) else DATA_ROOT) / "models"
LEGACY_ROOT = INSTALL_ROOT.parent / "Qwen3.8-27B"  # Optional sibling, read-only migration source.
DSH_DEFAULT_ROOT = DATA_ROOT / "dsh"
