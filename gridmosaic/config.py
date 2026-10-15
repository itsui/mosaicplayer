"""User settings, persisted with QSettings (~/.config/gridmosaic/gridmosaic.conf)."""

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

    @classmethod
    def load(cls) -> "Config":
        store = QSettings("gridmosaic", "gridmosaic")
        cfg = cls()
        for f in fields(cls):
            if not store.contains(f.name):
                continue
            default = getattr(cfg, f.name)
            try:
                value = _coerce(store.value(f.name), default)
            except (TypeError, ValueError):
                continue  # a damaged entry falls back to its default
            if f.name in CHOICES and value not in CHOICES[f.name]:
                continue
            if f.name in RANGES:
                if value != value:  # NaN
                    continue
                lo, hi = RANGES[f.name]
                value = min(max(value, lo), hi)
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
        return cfg

    def save(self) -> None:
        store = QSettings("gridmosaic", "gridmosaic")
        for f in fields(self):
            store.setValue(f.name, getattr(self, f.name))
        store.setValue("config_version", CONFIG_VERSION)
        store.remove("evenness")  # replaced by size_balance in v2
        store.sync()

    def add_recent(self, path: str) -> None:
        recent = [p for p in self.recent_files if p != path]
        self.recent_files = [path, *recent][: self.recent_limit]


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
