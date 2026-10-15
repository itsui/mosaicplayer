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
import re
import stat
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

VIDEO_EXTENSIONS = frozenset(
    ".3g2 .3gp .asf .avi .divx .dv .f4v .flv .gif .m2t .m2ts .m4v .mkv .mov .mp4"
    " .mpeg .mpg .mts .mxf .ogm .ogv .qt .rm .rmvb .ts .vob .webm .wmv".split()
)
PLAYLIST_EXTENSIONS = frozenset({".gpls", ".m3u", ".m3u8"})
GPLS_HEADER = "#GRIDPLAYER"
MIN_WEIGHT, MAX_WEIGHT = 0.25, 6.0  # a little beyond the UI's 0.35–4 steps


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


def local_path(item: str) -> str:
    """``file://`` URIs (VLC writes them in .m3u files) as plain, decoded paths.

    Left as URLs they skipped the missing-file check, showed "%20" in titles
    and had no folder for "random from the same folder".
    """
    if not item.lower().startswith("file:"):
        return item
    parsed = urllib.parse.urlparse(item)
    if parsed.netloc not in ("", "localhost"):
        return item  # a file on another host: leave it to mpv
    # surrogateescape keeps file names that aren't UTF-8 as their bytes on disk.
    return urllib.parse.unquote(parsed.path, errors="surrogateescape")


def natural_key(text: str):
    """Sort key that puts "clip 2" before "clip 10"."""
    return tuple(int(part) if i % 2 else part.casefold()
                 for i, part in enumerate(re.split(r"(\d+)", text)))


def read_gpls(path: str | os.PathLike) -> Playlist:
    # utf-8-sig: editors on Windows often add a byte-order mark.
    lines = [ln.strip() for ln in Path(path).read_text(encoding="utf-8-sig").splitlines()]
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
            uris.append(local_path(ln))

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
    # Through a symlink, write the real file instead of replacing the link.
    target = Path(path).resolve()
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
    except OSError:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.chmod(tmp, mode)  # mkstemp makes it private (0600)
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
    for ln in _m3u_lines(Path(path), base):
        ln = local_path(ln.strip())
        if not ln or ln.startswith("#"):
            continue
        p = ln if "://" in ln else str((base / ln).resolve())
        entries.append(Entry(p))
    return Playlist(entries)


def _m3u_lines(path: Path, base: Path) -> list[str]:
    """Lines of an .m3u in whatever encoding it uses.

    .m3u8 is UTF-8, but plain .m3u files are often Latin-1/Windows-1252. Lines
    that aren't UTF-8 are matched against the raw file-name bytes first (so they
    find the file as it is named on disk), then read as Windows-1252.
    """
    data = path.read_bytes().removeprefix(b"\xef\xbb\xbf")
    try:
        return data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        pass
    lines = []
    for raw in data.splitlines():
        try:
            lines.append(raw.decode("utf-8"))
            continue
        except UnicodeDecodeError:
            pass
        as_bytes = os.fsdecode(raw.strip())  # keeps undecodable bytes as-is
        if "://" not in as_bytes and (base / as_bytes).exists():
            lines.append(as_bytes)
        else:
            lines.append(raw.decode("cp1252", errors="replace"))
    return lines


def expand_inputs(inputs) -> tuple[list[Entry], dict]:
    """Turn files, folders (recursive) and playlists into entries.

    Returns the entries and the params of the first .gpls seen, if any.
    """
    entries: list[Entry] = []
    params: dict = {}
    for item in inputs:
        item = local_path(str(item))
        if "://" in item:
            entries.append(Entry(item))
            continue
        p = Path(item).expanduser()
        if p.is_dir():
            entries += [Entry(f) for f in _scan(p)]
        elif p.suffix.lower() == ".gpls":
            pl = read_gpls(p)
            params = params or pl.params
            entries += pl.entries
        elif p.suffix.lower() in {".m3u", ".m3u8"}:
            entries += read_m3u(p).entries
        elif p.is_file():
            entries.append(Entry(str(p)))
    return entries, params


def _scan(folder: Path) -> list[str]:
    """Videos under ``folder``, in natural order, skipping hidden folders.

    Hidden folders hold things like .Trash-1000 (deleted videos on a drive)
    and thumbnail caches. The folder itself may be hidden if chosen directly.
    """
    found = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        found += [os.path.join(root, f) for f in files if is_video(f)]
    return sorted(found, key=lambda f: natural_key(os.path.relpath(f, folder)))


def siblings(path: str) -> list[str]:
    """Other videos in the same folder, for "replace with random" in a tile."""
    folder = Path(path).parent
    try:
        return sorted((str(f) for f in folder.iterdir() if f.is_file() and is_video(f)),
                      key=natural_key)
    except OSError:
        return []


def next_in_folder(path: str) -> str | None:
    """The video after ``path`` in its folder (natural order, wrapping around).

    Matched by name, so a path spelled differently ("a//b.mp4") or a video
    renamed or deleted since it was opened still has a next one.
    """
    folder = siblings(path)
    if not folder:
        return None
    key = natural_key(Path(path).name)
    later = [f for f in folder if natural_key(Path(f).name) > key]
    return later[0] if later else folder[0]
