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
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

VIDEO_EXTENSIONS = frozenset(
    ".3g2 .3gp .asf .avi .divx .dv .f4v .flv .gif .m2t .m2ts .m4v .mkv .mov .mp4"
    " .mpeg .mpg .mts .mxf .ogm .ogv .qt .rm .rmvb .ts .vob .webm .wmv".split()
)
PLAYLIST_EXTENSIONS = frozenset({".gpls", ".m3u", ".m3u8"})
GPLS_HEADER = "#GRIDPLAYER"
MIN_WEIGHT, MAX_WEIGHT = 0.1, 10.0


@dataclass
class Entry:
    path: str
    muted: bool = True
    paused: bool = False
    volume: float = 1.0
    rate: float = 1.0
    start_random: bool = False
    position: float = 0.0  # seconds; GridPlayer stores milliseconds
    size_weight: float = 1.0  # relative tile area in puzzle layouts (GridMosaic only)
    extra: dict = field(default_factory=dict)  # untouched GridPlayer fields

    def to_gpls_params(self, save_position=True, save_paused=True) -> dict:
        params = dict(self.extra)
        params.update(is_muted=self.muted, volume=self.volume, rate=self.rate,
                      is_start_random=self.start_random)
        if save_paused:
            params["is_paused"] = self.paused
        if save_position and self.position > 0:
            params["current_position"] = int(self.position * 1000)
        if self.size_weight != 1.0:
            params["gm_size_weight"] = round(self.size_weight, 3)
        return params


@dataclass
class Playlist:
    entries: list[Entry] = field(default_factory=list)
    params: dict = field(default_factory=dict)  # the #P: line, kept for round trips


def is_video(path: str | os.PathLike) -> bool:
    name = Path(path).name
    # "._name.mp4" files are macOS metadata, not videos.
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS and not name.startswith("._")


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
            try:
                loaded = json.loads(ln[3:])
            except ValueError:
                loaded = {}
            params = loaded if isinstance(loaded, dict) else {}
        elif ln.startswith("#V"):
            idx, sep, data = ln[2:].partition(":")
            if not sep or not idx.isdigit():
                continue  # not a video-parameter line
            try:
                params_for_video = json.loads(data)
            except ValueError:
                continue  # a damaged line loses its settings, not the video
            if isinstance(params_for_video, dict):
                video_params[int(idx)] = params_for_video
        elif not ln.startswith("#"):
            uris.append(ln)

    base = Path(path).resolve().parent
    entries = []
    for idx, uri in enumerate(uris):
        vp = dict(video_params.get(idx, {}))
        if "://" not in uri and not Path(uri).is_absolute():
            uri = str((base / uri).resolve())  # relative to the playlist (GridPlayer #109)
        entries.append(Entry(
            path=uri,
            muted=bool(vp.pop("is_muted", True)),
            paused=bool(vp.pop("is_paused", False)),
            volume=_number(vp.pop("volume", 1.0), 1.0, 0.0, 1.5),
            rate=_number(vp.pop("rate", 1.0), 1.0, 0.1, 8.0),
            start_random=bool(vp.pop("is_start_random", False)),
            position=_number(vp.pop("current_position", 0), 0.0, 0.0, 1e12) / 1000,
            size_weight=_number(vp.pop("gm_size_weight", 1.0), 1.0, MIN_WEIGHT, MAX_WEIGHT),
            extra=vp,
        ))
    return Playlist(entries, params)


def write_gpls(path: str | os.PathLike, playlist: Playlist, save_positions=True,
               save_paused=True) -> None:
    lines = [GPLS_HEADER, "#P:" + json.dumps(playlist.params, separators=(",", ":"))]
    for idx, entry in enumerate(playlist.entries):
        params = entry.to_gpls_params(save_positions, save_paused)
        lines.append(f"#V{idx}:" + json.dumps(params, separators=(",", ":")))
    lines += [e.path for e in playlist.entries]
    # Write a temporary file and swap it in, so a crash or full disk can't leave
    # a half-written playlist behind.
    target = Path(path)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _number(value, default, lo, hi) -> float:
    """A numeric playlist field, clamped; anything unusable becomes ``default``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return min(max(number, lo), hi)


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
