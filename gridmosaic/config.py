"""User settings, persisted with QSettings (~/.config/gridmosaic/gridmosaic.conf)."""

from __future__ import annotations

from dataclasses import dataclass, field, fields

from PySide6.QtCore import QSettings

from .tile import AUTO, LOOP


@dataclass
class Config:
    # Layout
    layout_mode: str = "puzzle"
    gap: int = 2
    max_crop: float = 0.12
    fixed_cols: int = 0  # 0 = automatic
    fixed_rows: int = 0
    layout_effort: float = 1.2  # seconds spent refining the puzzle layout
    evenness: float = 1.0  # puzzle: how strongly to equalise tile sizes

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
            raw = store.value(f.name)
            default = getattr(cfg, f.name)
            setattr(cfg, f.name, _coerce(raw, default))
        return cfg

    def save(self) -> None:
        store = QSettings("gridmosaic", "gridmosaic")
        for f in fields(self):
            store.setValue(f.name, getattr(self, f.name))
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
        return list(raw) if isinstance(raw, (list, tuple)) else [raw]
    return str(raw)
