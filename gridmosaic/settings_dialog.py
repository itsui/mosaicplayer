"""Settings window."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFormLayout, QLabel, QPushButton, QSpinBox,
                               QTabWidget, QVBoxLayout, QWidget)

from .config import Config
from .layout import BALANCE_MODES, LAYOUT_MODES
from .options import DECODER_MODES, END_ACTIONS

RELOAD_FIELDS = ("decoder_mode", "hwdec_api", "gpu_budget_mpx", "max_gpu_tiles",
                 "auto_threshold_mpx", "scaling", "render_threads")


def _combo(options: dict, current: str) -> QComboBox:
    box = QComboBox()
    for key, label in options.items():
        box.addItem(label, key)
    box.setCurrentIndex(max(0, box.findData(current)))
    return box


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #888;")
    return label


class SettingsDialog(QDialog):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GridMosaic settings")
        self.setMinimumWidth(560)
        self._original = config
        self.result_config: Config | None = None
        c = config

        tabs = QTabWidget()

        # Layout
        self.layout_mode = _combo(LAYOUT_MODES, c.layout_mode)
        self.gap = QSpinBox(minimum=0, maximum=40, value=c.gap, suffix=" px")
        self.max_crop = QSpinBox(minimum=0, maximum=40, value=round(c.max_crop * 100), suffix=" %")
        self.fixed_cols = QSpinBox(minimum=0, maximum=20, value=c.fixed_cols, specialValueText="auto")
        self.fixed_rows = QSpinBox(minimum=0, maximum=20, value=c.fixed_rows, specialValueText="auto")
        self.balance = _combo(BALANCE_MODES, c.size_balance)
        self.effort = QDoubleSpinBox(minimum=0.2, maximum=10.0, singleStep=0.5,
                                     value=c.layout_effort, suffix=" s")
        form = QFormLayout()
        form.addRow("Packing mode", self.layout_mode)
        form.addRow("Gap between videos", self.gap)
        form.addRow("Allow cropping up to", self.max_crop)
        form.addRow(_note("A little cropping lets the puzzle and justified layouts fill the "
                          "screen with no black bars. 0% never crops."))
        form.addRow("Fixed grid columns", self.fixed_cols)
        form.addRow("Fixed grid rows", self.fixed_rows)
        form.addRow("Puzzle: tile sizes", self.balance)
        form.addRow(_note("Equal gives every video the same area; allowing ~15% crop lets it "
                          "fill the screen. Fill first covers more but sizes vary."))
        form.addRow("Puzzle: search time", self.effort)
        tabs.addTab(_page(form), "Layout")

        # Playback
        self.end_action = _combo(END_ACTIONS, c.end_action)
        self.start_muted = QCheckBox("Start new videos muted", checked=c.start_muted)
        self.start_random = QCheckBox("Start new videos at a random position", checked=c.start_random)
        self.pause_min = QCheckBox("Pause everything when minimised", checked=c.pause_when_minimized)
        form = QFormLayout()
        form.addRow("When a video ends", self.end_action)
        form.addRow(self.start_muted)
        form.addRow(self.start_random)
        form.addRow(self.pause_min)
        tabs.addTab(_page(form), "Playback")

        # Decoding
        self.decoder_mode = _combo(DECODER_MODES, c.decoder_mode)
        self.hwdec_api = _combo({"nvdec": "NVIDIA (NVDEC)", "vaapi": "VA-API (Intel/AMD)",
                                 "vulkan": "Vulkan video"}, c.hwdec_api)
        self.budget = QSpinBox(minimum=50, maximum=20000, singleStep=100,
                               value=int(c.gpu_budget_mpx), suffix=" Mpx/s")
        self.max_gpu = QSpinBox(minimum=0, maximum=200, value=c.max_gpu_tiles)
        self.threshold = QSpinBox(minimum=0, maximum=2000, singleStep=10,
                                  value=int(c.auto_threshold_mpx), suffix=" Mpx/s")
        self.scaling = _combo({"balanced": "Balanced — cheap downscaling (default)",
                               "quality": "Quality — spline36 / mitchell everywhere",
                               "fast": "Fast — bilinear everywhere"}, c.scaling)
        form = QFormLayout()
        form.addRow("Decoder", self.decoder_mode)
        form.addRow("GPU API", self.hwdec_api)
        form.addRow("GPU decode budget", self.budget)
        form.addRow("Max videos on the GPU", self.max_gpu)
        form.addRow("Auto: GPU only above", self.threshold)
        form.addRow(_note("1080p at 30 fps is about 62 Mpx/s. Each GPU-decoded video also "
                          "costs a few hundred MB of video memory."))
        form.addRow("Scaling", self.scaling)
        self.render_threads = QSpinBox(minimum=1, maximum=16, value=c.render_threads)
        form.addRow("Render threads", self.render_threads)
        form.addRow(_note("More threads keep many tiles smooth; 4 suits ~60 videos."))
        form.addRow(_note("Changing decoder or scaling settings reloads all videos."))
        tabs.addTab(_page(form), "Decoding")

        # Interface
        self.titles = _combo({"hover": "Show titles on hover", "always": "Always show titles",
                              "never": "Only show progress on hover"}, c.titles)
        self.hide_cursor = QSpinBox(minimum=0, maximum=60000, singleStep=500,
                                    value=c.hide_cursor_ms, suffix=" ms", specialValueText="never")
        self.on_top = QCheckBox("Keep window on top", checked=c.stay_on_top)
        self.menubar = QCheckBox("Show menu bar (right-click or Ctrl+M when hidden)", checked=c.show_menubar)
        self.start_fs = QCheckBox("Start in fullscreen", checked=c.start_fullscreen)
        self._background = QColor(c.background)
        self.bg_button = QPushButton()
        self.bg_button.clicked.connect(self._pick_background)
        self._update_bg_button()
        form = QFormLayout()
        form.addRow("Titles", self.titles)
        form.addRow("Hide mouse after", self.hide_cursor)
        form.addRow("Background", self.bg_button)
        form.addRow(self.on_top)
        form.addRow(self.menubar)
        form.addRow(self.start_fs)
        tabs.addTab(_page(form), "Interface")

        # Playlists
        self.save_positions = QCheckBox("Save playback positions in playlists",
                                        checked=c.save_positions)
        self.save_paused = QCheckBox("Save paused/playing state in playlists", checked=c.save_paused)
        self.recent_limit = QSpinBox(minimum=0, maximum=50, value=c.recent_limit)
        form = QFormLayout()
        form.addRow(self.save_positions)
        form.addRow(self.save_paused)
        form.addRow("Recent playlists to remember", self.recent_limit)
        form.addRow(_note("Playlists are saved as GridPlayer .gpls files, so they open in "
                          "GridPlayer too."))
        tabs.addTab(_page(form), "Playlists")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel
                                   | QDialogButtonBox.RestoreDefaults)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self._defaults)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    def _pick_background(self):
        color = QColorDialog.getColor(self._background, self, "Background colour")
        if color.isValid():
            self._background = color
            self._update_bg_button()

    def _update_bg_button(self):
        self.bg_button.setText(self._background.name())
        self.bg_button.setStyleSheet(
            f"background: {self._background.name()}; color: "
            f"{'black' if self._background.lightness() > 128 else 'white'};")

    def _defaults(self):
        """Show the default values; OK applies them, Cancel keeps the current ones."""
        d = Config()
        for box, value in ((self.layout_mode, d.layout_mode), (self.balance, d.size_balance),
                           (self.end_action, d.end_action), (self.decoder_mode, d.decoder_mode),
                           (self.hwdec_api, d.hwdec_api), (self.scaling, d.scaling),
                           (self.titles, d.titles)):
            box.setCurrentIndex(max(0, box.findData(value)))
        for spin, value in ((self.gap, d.gap), (self.max_crop, round(d.max_crop * 100)),
                            (self.fixed_cols, d.fixed_cols), (self.fixed_rows, d.fixed_rows),
                            (self.effort, d.layout_effort), (self.budget, int(d.gpu_budget_mpx)),
                            (self.max_gpu, d.max_gpu_tiles),
                            (self.threshold, int(d.auto_threshold_mpx)),
                            (self.render_threads, d.render_threads),
                            (self.hide_cursor, d.hide_cursor_ms),
                            (self.recent_limit, d.recent_limit)):
            spin.setValue(value)
        for check, value in ((self.start_muted, d.start_muted), (self.start_random, d.start_random),
                             (self.pause_min, d.pause_when_minimized), (self.on_top, d.stay_on_top),
                             (self.menubar, d.show_menubar), (self.start_fs, d.start_fullscreen),
                             (self.save_positions, d.save_positions),
                             (self.save_paused, d.save_paused)):
            check.setChecked(value)
        self._background = QColor(d.background)
        self._update_bg_button()

    def _accept(self):
        self.result_config = replace(
            self._original,
            layout_mode=self.layout_mode.currentData(),
            gap=self.gap.value(),
            max_crop=self.max_crop.value() / 100,
            fixed_cols=self.fixed_cols.value(),
            fixed_rows=self.fixed_rows.value(),
            size_balance=self.balance.currentData(),
            layout_effort=self.effort.value(),
            end_action=self.end_action.currentData(),
            start_muted=self.start_muted.isChecked(),
            start_random=self.start_random.isChecked(),
            pause_when_minimized=self.pause_min.isChecked(),
            decoder_mode=self.decoder_mode.currentData(),
            hwdec_api=self.hwdec_api.currentData(),
            gpu_budget_mpx=float(self.budget.value()),
            max_gpu_tiles=self.max_gpu.value(),
            auto_threshold_mpx=float(self.threshold.value()),
            scaling=self.scaling.currentData(),
            render_threads=self.render_threads.value(),
            titles=self.titles.currentData(),
            hide_cursor_ms=self.hide_cursor.value(),
            stay_on_top=self.on_top.isChecked(),
            show_menubar=self.menubar.isChecked(),
            start_fullscreen=self.start_fs.isChecked(),
            background=self._background.name(),
            save_positions=self.save_positions.isChecked(),
            save_paused=self.save_paused.isChecked(),
            recent_limit=self.recent_limit.value(),
            recent_files=self._original.recent_files[:self.recent_limit.value()],
        )
        self.accept()


def _page(form: QFormLayout) -> QWidget:
    page = QWidget()
    page.setLayout(form)
    return page
