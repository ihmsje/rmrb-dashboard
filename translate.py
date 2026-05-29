"""Per-day translation cache (Chinese source string -> English).

Each date has its own file `data/translations_<date>.json`, mirroring the
per-date edition files. Loaded results are cached in memory and refreshed
when the file's mtime changes, so a daily job that writes a new file is
picked up without restarting the server.
"""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

_cache: dict[str, tuple[float, dict[str, str]]] = {}


def _path(date_str: str) -> str:
    return os.path.join(DATA_DIR, f"translations_{date_str}.json")


def load_translations(date_str: str) -> dict[str, str]:
    """Return the translation table for a date, or {} if none exists."""
    path = _path(date_str)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}

    cached = _cache.get(date_str)
    if cached and cached[0] == mtime:
        return cached[1]

    with open(path, "r", encoding="utf-8") as f:
        table = json.load(f)
    _cache[date_str] = (mtime, table)
    return table
