"""Log file and update check (for bug reports and new releases)."""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
import sys
import urllib.request
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from . import __version__

RELEASES_API = "https://api.github.com/repos/itsui/mosaicplayer/releases/latest"
RELEASES_PAGE = "https://github.com/itsui/mosaicplayer/releases"


def log_dir() -> Path:
    """~/.local/share/mosaicplayer on Linux, %LOCALAPPDATA%\\mosaicplayer on Windows."""
    base = QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)
    return Path(base) / "mosaicplayer"


def setup_logging(debug: bool) -> None:
    """Log to the terminal and to mosaicplayer.log (kept small, last two runs' worth)."""
    terminal = logging.StreamHandler()
    terminal.setLevel(logging.DEBUG if debug else logging.WARNING)
    handlers: list[logging.Handler] = [terminal]
    try:
        folder = log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            folder / "mosaicplayer.log", maxBytes=1_000_000, backupCount=1, encoding="utf-8"))
    except OSError:
        pass  # read-only home or similar: terminal only
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("mosaicplayer").info("MosaicPlayer %s on %s", __version__, sys.platform)

    def log_uncaught(kind, value, tb):
        logging.getLogger("mosaicplayer").error("Unexpected error", exc_info=(kind, value, tb))

    sys.excepthook = log_uncaught  # errors in Qt callbacks end up in the log too


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def latest_release(timeout=8.0) -> tuple[str, str] | None:
    """(version, page URL) of the newest release if newer than this one, else None.

    Raises OSError / ValueError when GitHub can't be reached or answers oddly.
    """
    request = urllib.request.Request(RELEASES_API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"MosaicPlayer/{__version__}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.load(response)
    tag = str(data.get("tag_name", ""))
    if _version_tuple(tag) > _version_tuple(__version__):
        return tag.lstrip("v"), str(data.get("html_url") or RELEASES_PAGE)
    return None
