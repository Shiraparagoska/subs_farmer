"""Пути к рабочим файлам: в dev — текущая папка, в сборке PyInstaller — рядом с exe."""

from __future__ import annotations

import sys
from pathlib import Path


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()
