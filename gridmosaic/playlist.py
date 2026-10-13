"""Playlist input/output.

Reads and writes GridPlayer's ``.gpls`` format so playlists move freely between
the two apps:

    #GRIDPLAYER
    #P:{playlist json}
    #V0:{video json}
    #V1:{video json}
    /path/to/video0.mp4
    /path/to/video1.mp4

Fields GridMosaic doesn't use are kept and written back unchanged.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

VIDEO_EXTENSIONS = frozenset(
    ".3g2 .3gp .asf .avi .divx .dv .f4v .flv .gif .m2t .m2ts .m4v .mkv .mov .mp4"
    " .mpeg .mpg .mts .mxf .ogm .ogv .qt .rm .rmvb .ts .vob .webm .wmv".split()
)
PLAYLIST_EXTENSIONS = frozenset({".gpls", ".m3u", ".m3u8"})
GPLS_HEADER = "#GRIDPLAYER"


@dataclass
class Entry:
    path: str
    muted: bool = True
    paused: bool = False
    volume: float = 1.0
    rate: float = 1.0
    start_random: bool = False
    extra: dict = field(default_factory=dict)  # untouched GridPlayer fields

    def to_gpls_params(self) -> dict:
        params = dict(self.extra)
        params.update(is_muted=self.muted, is_paused=self.paused, volume=self.volume,
                      rate=self.rate, is_start_random=self.start_random)
        return params


@dataclass
class Playlist:
    entries: list[Entry] = field(default_factory=list)
    params: dict = field(default_factory=dict)  # the #P: line, kept for round trips


def is_video(path: str | os.PathLike) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def read_gpls(path: str | os.PathLike) -> Playlist:
    lines = [ln.strip() for ln in Path(path).read_text(encoding="utf-8").splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines or lines[0] != GPLS_HEADER:
        raise ValueError(f"{path} is not a GridPlayer playlist")

    params: dict = {}
    video_params: dict[int, dict] = {}
    uris: list[str] = []
    for ln in lines[1:]:
        if ln.startswith("#P:"):
            params = json.loads(ln[3:])
        elif ln.startswith("#V"):
            idx, data = ln[2:].split(":", 1)
            video_params[int(idx)] = json.loads(data)
        elif not ln.startswith("#"):
            uris.append(ln)

    entries = []
    for idx, uri in enumerate(uris):
        vp = dict(video_params.get(idx, {}))
        entries.append(Entry(
            path=uri,
            muted=vp.pop("is_muted", True),
            paused=vp.pop("is_paused", False),
            volume=vp.pop("volume", 1.0),
            rate=vp.pop("rate", 1.0),
            start_random=vp.pop("is_start_random", False),
            extra=vp,
        ))
    return Playlist(entries, params)


def write_gpls(path: str | os.PathLike, playlist: Playlist) -> None:
    lines = [GPLS_HEADER, "#P:" + json.dumps(playlist.params, separators=(",", ":"))]
    for idx, entry in enumerate(playlist.entries):
        lines.append(f"#V{idx}:" + json.dumps(entry.to_gpls_params(), separators=(",", ":")))
    lines += [e.path for e in playlist.entries]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_m3u(path: str | os.PathLike) -> Playlist:
    base = Path(path).parent
    entries = []
    for ln in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        p = ln if "://" in ln else str((base / ln).resolve())
        entries.append(Entry(p))
    return Playlist(entries)


def expand_inputs(inputs) -> tuple[list[Entry], dict]:
    """Turn files, folders (recursive) and playlists into entries.

    Returns the entries and the params of the first .gpls seen, if any.
    """
    entries: list[Entry] = []
    params: dict = {}
    for item in inputs:
        item = str(item)
        if "://" in item and not item.startswith("file://"):
            entries.append(Entry(item))
            continue
        p = Path(item.removeprefix("file://")).expanduser()
        if p.is_dir():
            files = sorted(f for f in p.rglob("*") if f.is_file() and is_video(f))
            entries += [Entry(str(f)) for f in files]
        elif p.suffix.lower() == ".gpls":
            pl = read_gpls(p)
            params = params or pl.params
            entries += pl.entries
        elif p.suffix.lower() in {".m3u", ".m3u8"}:
            entries += read_m3u(p).entries
        elif p.is_file():
            entries.append(Entry(str(p)))
    return entries, params


def siblings(path: str) -> list[str]:
    """Other videos in the same folder, for "replace with random" in a tile."""
    folder = Path(path).parent
    try:
        return sorted(str(f) for f in folder.iterdir() if f.is_file() and is_video(f))
    except OSError:
        return []
