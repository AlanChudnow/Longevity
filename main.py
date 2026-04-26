"""
main.py — Application entry point.

Ensures the CDC life table cache exists, then launches the GUI.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.health_models import (
    _CACHE_PATH, _ZIP_CACHE_PATH,
    prime_life_table_cache, prime_zip_cache,
)


def ensure_life_table() -> None:
    if not _CACHE_PATH.exists():
        print("Life table cache not found — downloading from CDC (one-time setup)...")
        prime_life_table_cache()
        print("Download complete.\n")


def ensure_zip_cache() -> None:
    if not _ZIP_CACHE_PATH.exists():
        print("ZIP life-expectancy cache not found — downloading (one-time setup)...")
        prime_zip_cache()
        print("ZIP download complete.\n")


def main() -> None:
    ensure_life_table()
    ensure_zip_cache()
    from src.gui import main as gui_main
    gui_main()


if __name__ == "__main__":
    main()
