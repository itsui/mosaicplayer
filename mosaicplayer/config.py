"""User settings, persisted with QSettings (~/.config/mosaicplayer/mosaicplayer.conf)."""

from __future__ import annotations

from dataclasses import dataclass, field, fields

from PySide6.QtCore import QSettings

from .options import AUTO, LOOP

CONFIG_VERSION = 2

# Allowed values for the settings that pick from a fixed set.
CHOICES = {
    "layout_mode": {"puzzle", "rows", "columns", "grid", "fixed-grid"},
    "size_balance": {"equal", "balanced", "fill"},
    "end_action": {"loop", "next", "random", "stop"},
    "decoder_mode": {"auto", "gpu-copy", "gpu-zero-copy", "cpu"},
    "hwdec_api": {"nvdec", "vaapi", "vulkan"},
    "scaling": {"balanced", "quality", "fast"},
    "titles": {"hover", "always", "never"},
}

# Allowed ranges for numbers (the same as in the settings window). Values out
# of range in a hand-edited file are clamped; NaN falls back to the default.
RANGES = {
    "gap": (0, 40),
    "max_crop": (0.0, 0.4),
    "fixed_cols": (0, 20),
    "fixed_rows": (0, 20),
    "layout_effort": (0.2, 10.0),
    "gpu_budget_mpx": (50.0, 20000.0),
    "max_gpu_tiles": (0, 200),
    "auto_threshold_mpx": (0.0, 2000.0),
    "render_threads": (1, 16),
    "hide_cursor_ms": (0, 60000),
    "recent_limit": (0, 50),
}


# Settings that belong to a playlist: saved in it and restored when it is
# opened. Changing them from the menus or keys affects the current playlist;
# they become defaults only through "Save current settings as default" (or the
# settings window).
PLAYLIST_FIELDS = ("layout_mode", "size_balance", "max_crop", "gap", "fixed_cols", "fixed_rows",
                   "end_action")

# Settings files from before the rename (GridMosaic): (old, new) QSettings names.
_RENAMED = ((("gridmosaic", "gridmosaic"), ("mosaicplayer", "mosaicplayer")),
            (("gridmosaic", "window"), ("mosaicplayer", "window")))


def import_old_settings() -> None:
    """Copy settings saved under the old name the first time MosaicPlayer runs."""
    for old_name, new_name in _RENAMED:
        new = QSettings(*new_name)
        if new.allKeys():
            continue
        old = QSettings(*old_name)
        for key in old.allKeys():
            new.setValue(key, old.value(key))
        new.sync()


@dataclass
class Config:
    # Layout
    layout_mode: str = "puzzle"
    gap: int = 2
    max_crop: float = 0.15
    fixed_cols: int = 0  # 0 = automatic
    fixed_rows: int = 0
    size_balance: str = "equal"  # puzzle: equal / balanced / fill
    layout_effort: float = 2.5  # seconds the puzzle may spend refining in the background

    # Playback
    start_muted: bool = True
    end_action: str = LOOP
    start_random: bool = False
    pause_when_minimized: bool = True

    # Decoding
    decoder_mode: str = AUTO
    hwdec_api: str = "nvdec"
    gpu_budget_mpx: float = 1200.0
    max_gpu_tiles: int = 24
    auto_threshold_mpx: float = 70.0
    scaling: str = "balanced"
    render_threads: int = 4

    # Interface
    titles: str = "hover"  # never / hover / always
    hide_cursor_ms: int = 2500
    stay_on_top: bool = False
    show_menubar: bool = True
    start_fullscreen: bool = False
    background: str = "#000000"

    # Playlists
    save_positions: bool = True
    save_paused: bool = False
    recent_files: list = field(default_factory=list)
    recent_limit: int = 12

    def __post_init__(self):
        # The saved defaults for the playlist settings (not a dataclass field).
        self._defaults = self.playlist_settings()

    @classmethod
    def load(cls) -> "Config":
        store = QSettings("mosaicplayer", "mosaicplayer")
        cfg = cls()
        for f in fields(cls):
            if not store.contains(f.name):
                continue
            value = _valid(f.name, store.value(f.name), getattr(cfg, f.name))
            if value is not None:
                setattr(cfg, f.name, value)
        try:
            version = int(store.value("config_version", 1))
        except (TypeError, ValueError):
            version = 1
        if version < 2:
            # v2: equal-area puzzle. Move values still at the old defaults.
            if abs(cfg.max_crop - 0.12) < 1e-9:
                cfg.max_crop = 0.15
            if abs(cfg.layout_effort - 1.2) < 1e-9:
                cfg.layout_effort = 2.5
        cfg._defaults = cfg.playlist_settings()
        return cfg

    def save(self) -> None:
        """Save the settings; playlist settings are saved as their defaults."""
        store = QSettings("mosaicplayer", "mosaicplayer")
        for f in fields(self):
            value = self._defaults.get(f.name, getattr(self, f.name))
            store.setValue(f.name, value)
        store.setValue("config_version", CONFIG_VERSION)
        store.remove("evenness")  # replaced by size_balance in v2
        store.sync()

    # --- playlist settings ---------------------------------------------------

    def playlist_settings(self) -> dict:
        return {name: getattr(self, name) for name in PLAYLIST_FIELDS}

    def apply_playlist_settings(self, settings) -> None:
        """Use the settings saved in a playlist (invalid or unknown ones are skipped)."""
        if not isinstance(settings, dict):
            return
        for name in PLAYLIST_FIELDS:
            if name in settings:
                value = _valid(name, settings[name], getattr(self, name))
                if value is not None:
                    setattr(self, name, value)

    def use_defaults(self) -> None:
        for name, value in self._defaults.items():
            setattr(self, name, value)

    def is_default(self) -> bool:
        return self.playlist_settings() == self._defaults

    def save_as_default(self, names=PLAYLIST_FIELDS) -> None:
        for name in names:
            self._defaults[name] = getattr(self, name)
        self.save()

    def add_recent(self, path: str) -> None:
        recent = [p for p in self.recent_files if p != path]
        self.recent_files = [path, *recent][: self.recent_limit]


def _valid(name, raw, default):
    """``raw`` converted to the type of ``default`` and checked; None if unusable."""
    if isinstance(default, (int, float)) and not isinstance(default, bool) \
            and isinstance(raw, bool):
        return None  # JSON true/false is not a number here
    try:
        value = _coerce(raw, default)
    except (TypeError, ValueError, OverflowError):
        return None  # a damaged entry falls back to its default
    if name in CHOICES and value not in CHOICES[name]:
        return None
    if name in RANGES:
        if value != value:  # NaN
            return None
        lo, hi = RANGES[name]
        value = min(max(value, lo), hi)
    return value


def _coerce(raw, default):
    """QSettings returns strings from INI files; convert back to the field type."""
    if isinstance(default, bool):
        return raw if isinstance(raw, bool) else str(raw).lower() in ("true", "1", "yes")
    if isinstance(default, int):
        return int(float(raw))
    if isinstance(default, float):
        return float(raw)
    if isinstance(default, list):
        if raw is None:
            return []
        return [str(x) for x in raw] if isinstance(raw, (list, tuple)) else [str(raw)]
    return str(raw)
