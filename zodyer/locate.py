"""Hitta mpv.exe när den inte ligger i PATH.

shinchiro.mpv (winget) är ett vanligt installationsprogram och lägger sig i
Program Files UTAN att hamna i PATH. Förr löste bara start.py det och skickade
``--mpv`` vidare; startas zodyer direkt (pipx) måste paketet klara det själv.

Bara standardbiblioteket: start.py importerar konstanterna härifrån innan
beroendena är installerade.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

MPV_KNOWN_PATHS = (
    r"C:\Program Files\MPV Player\mpv.exe",
    r"C:\Program Files\mpv\mpv.exe",
    r"C:\Program Files (x86)\MPV Player\mpv.exe",
    r"C:\Program Files (x86)\mpv\mpv.exe",
)
MPV_SEARCH_ROOTS = (
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    "%LOCALAPPDATA%\\Programs",
    "%LOCALAPPDATA%\\Microsoft\\WinGet\\Packages",
)


def find_mpv(
    known_paths: tuple[str, ...] = MPV_KNOWN_PATHS,
    search_roots: tuple[str, ...] = MPV_SEARCH_ROOTS,
) -> Path | None:
    """PATH först, sedan kända platser, sist en grund sökning (två nivåer).

    Ingen plattformskontroll: sökvägarna finns helt enkelt inte på annat än
    Windows, och då går funktionen att testa överallt.
    """
    found = shutil.which("mpv")
    if found:
        return Path(found)

    for candidate in known_paths:
        path = Path(candidate)
        if path.is_file():
            return path

    for root in search_roots:
        base = Path(os.path.expandvars(root))
        if not base.is_dir():
            continue
        try:
            for pattern in ("*/mpv.exe", "*/*/mpv.exe"):
                for path in base.glob(pattern):
                    return path
        except OSError:
            continue
    return None
