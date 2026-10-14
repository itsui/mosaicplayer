"""Main window: menus, shortcuts, drag and drop, open/save."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (QDialog, QFileDialog, QInputDialog, QLabel, QMainWindow,
                               QMenu, QMessageBox)

from . import __version__
from .canvas import Canvas
from .config import Config
from .layout import BALANCE_MODES, LAYOUT_MODES
from .playlist import Entry, Playlist, expand_inputs, is_video, write_gpls
from .settings_dialog import RELOAD_FIELDS, SettingsDialog
from .tile import END_ACTIONS

HELP = """<h3>GridMosaic keys</h3><table cellspacing=4>
<tr><td><b>Space</b></td><td>pause / play all</td><td><b>Click</b></td><td>pause one</td></tr>
<tr><td><b>M</b></td><td>mute / unmute all</td><td><b>Middle-click</b></td><td>mute / unmute one</td></tr>
<tr><td><b>Wheel</b></td><td>seek ±5 s (Shift ±30 s)</td><td><b>← →</b></td><td>seek hovered video</td></tr>
<tr><td><b>Ctrl+wheel</b></td><td>zoom into the hovered video</td><td><b>Z</b></td><td>reset zoom</td></tr>
<tr><td><b>Double-click</b>, <b>Enter</b></td><td>focus one video</td><td><b>Esc</b></td><td>show all / leave fullscreen</td></tr>
<tr><td><b>Drag</b> a video</td><td>swap it with another</td><td><b>Del</b></td><td>remove hovered video</td></tr>
<tr><td><b>1</b>–<b>5</b></td><td>puzzle, rows, columns, grid, fixed grid</td><td><b>R</b></td><td>reshuffle puzzle</td></tr>
<tr><td><b>C</b></td><td>allow cropping on / off</td><td><b>[ ]</b></td><td>gap smaller / bigger</td></tr>
<tr><td><b>B</b></td><td>puzzle tile sizes: equal / balanced / fill</td><td></td><td></td></tr>
<tr><td><b>N</b></td><td>random video from the same folder</td><td><b>S</b></td><td>random positions for all</td></tr>
<tr><td><b>Y</b></td><td>sync all to hovered video's time</td><td><b>Home</b></td><td>restart all</td></tr>
<tr><td><b>F</b>, <b>F11</b></td><td>fullscreen</td><td><b>H</b></td><td>stats overlay</td></tr>
<tr><td><b>Ctrl+O</b></td><td>open videos</td><td><b>Ctrl+Shift+O</b></td><td>add folder (with subfolders)</td></tr>
<tr><td><b>Ctrl+S</b></td><td>save playlist</td><td><b>Ctrl+,</b></td><td>settings</td></tr>
</table>"""

SPEEDS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0)
PLAYLIST_FILTER = "Playlists (*.gpls *.m3u *.m3u8)"
VIDEO_FILTER = ("Videos and playlists (*.mp4 *.mkv *.webm *.mov *.avi *.m4v *.wmv *.flv *.ts "
                "*.mpg *.mpeg *.gif *.gpls *.m3u *.m3u8);;All files (*)")


class MainWindow(QMainWindow):
    def __init__(self, config: Config):
        super().__init__()
        self.cfg = config
        self.setWindowTitle("GridMosaic")
        self.resize(1600, 900)
        self.setAcceptDrops(True)
        self._store = QSettings("gridmosaic", "window")
        self._playlist_params: dict = {}
        self._playlist_path: str | None = None
        self._paused_by_minimize = False

        self.canvas = Canvas(config, self)
        self.setCentralWidget(self.canvas)
        self.canvas.tiles_changed.connect(self._update_title)
        self.canvas.message.connect(self.toast)

        self._toast = QLabel(self)
        self._toast.setStyleSheet(
            "background: rgba(0,0,0,200); color: white; padding: 10px 16px; border-radius: 6px;")
        self._toast.hide()
        self._toast_timer = QTimer(self, singleShot=True)
        self._toast_timer.timeout.connect(self._toast.hide)

        self._empty = QLabel(
            "Drop videos, folders or a .gpls playlist here\n\n"
            "Ctrl+O to open  •  F1 for keys  •  Ctrl+, for settings", self.canvas)
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setStyleSheet("color: #888; font-size: 20px; background: transparent;")
        self._empty.setAttribute(Qt.WA_TransparentForMouseEvents)

        self._build_menus()
        self._apply_window_config()
        geometry = self._store.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    # --- menus --------------------------------------------------------------

    def _act(self, menu: QMenu | None, text, slot, shortcuts=(), checkable=False, checked=False):
        act = QAction(text, self)
        if shortcuts:
            act.setShortcuts([QKeySequence(s) for s in shortcuts])
            act.setShortcutContext(Qt.ApplicationShortcut)
        act.setCheckable(checkable)
        if checkable:
            act.setChecked(checked)
            act.toggled.connect(slot)
        else:
            act.triggered.connect(slot)
        self.addAction(act)  # shortcuts keep working with the menu bar hidden
        if menu is not None:
            menu.addAction(act)
        return act

    def _hovered(self, fn):
        def run(*_):
            tile = self.canvas.hovered
            if tile is not None:
                fn(tile)
        return run

    def _build_menus(self):
        c = self.canvas
        bar = self.menuBar()

        m = self.file_menu = bar.addMenu("&File")
        self._act(m, "Open videos…", self.open_files, ["Ctrl+O"])
        self._act(m, "Add folder (with subfolders)…", self.open_folder, ["Ctrl+Shift+O"])
        self._act(m, "Open URL…", self.open_url, ["Ctrl+U"])
        self._act(m, "Open playlist…", self.open_playlist, ["Ctrl+L"])
        self.recent_menu = m.addMenu("Recent playlists")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        m.addSeparator()
        self._act(m, "Save playlist", self.save_playlist, ["Ctrl+S"])
        self._act(m, "Save playlist as…", lambda: self.save_playlist(ask=True), ["Ctrl+Shift+S"])
        m.addSeparator()
        self._act(m, "Close all videos", self.close_all, ["Ctrl+W"])
        self._act(m, "Quit", self.close, ["Ctrl+Q"])

        m = self.play_menu = bar.addMenu("&Playback")
        self._act(m, "Pause / play all", lambda: c.set_all_paused(not c.all_paused()), ["Space"])
        self._act(m, "Mute / unmute all", self.toggle_mute_all, ["M"])
        self._act(m, "Restart all", c.restart_all, ["Home"])
        self._act(m, "Random positions for all", lambda: [t.seek_random() for t in c.tiles], ["S"])
        self._act(m, "Sync all to hovered video", self._hovered(c.sync_to), ["Y"])
        speed = m.addMenu("Speed for all")
        for rate in SPEEDS:
            self._act(speed, f"{rate:g}×", lambda _=False, r=rate: c.set_all_speed(r))
        ends = m.addMenu("When a video ends")
        group = QActionGroup(self)
        self.end_actions = {}
        for key, label in END_ACTIONS.items():
            act = self._act(ends, label, lambda on, k=key: on and self._set_end_action(k),
                            checkable=True, checked=self.cfg.end_action == key)
            group.addAction(act)
            self.end_actions[key] = act
        m.addSeparator()
        self._act(m, "Seek hovered back 5 s", self._hovered(lambda t: t.seek(-5)), ["Left"])
        self._act(m, "Seek hovered forward 5 s", self._hovered(lambda t: t.seek(5)), ["Right"])
        self._act(m, "Replace hovered with random from its folder",
                  self._hovered(c.replace_with_random), ["N"])
        self._act(m, "Reset zoom of hovered video", self._hovered(lambda t: t.reset_zoom()), ["Z"])
        self._act(m, "Remove hovered video", self._hovered(c.remove_tile), ["Del"])

        m = self.layout_menu = bar.addMenu("&Layout")
        group = QActionGroup(self)
        self.mode_actions = {}
        for i, (key, label) in enumerate(LAYOUT_MODES.items(), start=1):
            act = self._act(m, label, lambda on, k=key: on and self.set_layout_mode(k), [str(i)],
                            checkable=True, checked=self.cfg.layout_mode == key)
            group.addAction(act)
            self.mode_actions[key] = act
        sizes = m.addMenu("Puzzle tile sizes")
        group = QActionGroup(self)
        self.balance_actions = {}
        for key, label in BALANCE_MODES.items():
            act = self._act(sizes, label, lambda on, k=key: on and self.set_balance(k),
                            checkable=True, checked=self.cfg.size_balance == key)
            group.addAction(act)
            self.balance_actions[key] = act
        self._act(None, "Cycle tile sizes", self.cycle_balance, ["B"])
        m.addSeparator()
        self._act(m, "Reshuffle puzzle", lambda: c.schedule_relayout(reshuffle=True), ["R"])
        self._act(m, "Shuffle playlist order", c.shuffle_order)
        self._act(m, "Sort by name", c.sort_by_name)
        self._act(m, "Fixed grid size…", self.ask_fixed_grid)
        m.addSeparator()
        self.crop_action = self._act(m, "Allow cropping to fill gaps", self.set_crop, ["C"],
                                     checkable=True, checked=self.cfg.max_crop > 0)
        self._act(m, "Smaller gap", lambda: self.set_gap(self.cfg.gap - 2), ["["])
        self._act(m, "Bigger gap", lambda: self.set_gap(self.cfg.gap + 2), ["]"])
        m.addSeparator()
        self._act(m, "Focus hovered video", self._hovered(c.set_solo), ["Return", "Enter"])
        self._act(m, "Show all", lambda: c.set_solo(None))

        m = self.view_menu = bar.addMenu("&View")
        self._act(m, "Fullscreen", self.toggle_fullscreen, ["F", "F11"])
        self.top_action = self._act(m, "Stay on top", self.set_stay_on_top, ["T"],
                                    checkable=True, checked=self.cfg.stay_on_top)
        self.menubar_action = self._act(m, "Show menu bar", self.set_menubar, ["Ctrl+M"],
                                        checkable=True, checked=self.cfg.show_menubar)
        self.hud_action = self._act(m, "Stats overlay", self.set_hud, ["H"], checkable=True)
        titles = m.addMenu("Titles")
        group = QActionGroup(self)
        for key, label in (("hover", "On hover"), ("always", "Always"), ("never", "Never")):
            act = self._act(titles, label, lambda on, k=key: on and self._set_titles(k),
                            checkable=True, checked=self.cfg.titles == key)
            group.addAction(act)
        self._act(None, "Leave", self.escape, ["Esc"])

        m = self.settings_menu = bar.addMenu("&Settings")
        self._act(m, "Settings…", self.open_settings, ["Ctrl+,"])

        m = self.help_menu = bar.addMenu("&Help")
        self._act(m, "Keyboard and mouse", self.show_help, ["F1", "?"])
        self._act(m, "About GridMosaic", self.show_about)

    def _fill_recent(self):
        self.recent_menu.clear()
        recent = [p for p in self.cfg.recent_files if Path(p).exists()]
        if not recent:
            self.recent_menu.addAction("(none)").setEnabled(False)
        for path in recent:
            self.recent_menu.addAction(Path(path).name, lambda p=path: self.open_playlist_path(p))
        if recent:
            self.recent_menu.addSeparator()
            self.recent_menu.addAction("Clear list", self._clear_recent)

    def _clear_recent(self):
        self.cfg.recent_files = []
        self.cfg.save()

    def contextMenuEvent(self, event):
        c = self.canvas
        tile = c.tile_at(c.mapFromGlobal(event.globalPos()).toPointF())
        menu = QMenu(self)
        if tile:
            menu.addAction("Unmute" if tile.entry.muted else "Mute",
                           lambda: (tile.set_muted(not tile.entry.muted), c.update()))
            menu.addAction("Play" if tile.paused else "Pause", tile.toggle_pause)
            menu.addAction("Restart", lambda: tile.seek_to(0))
            menu.addAction("Jump to random position", tile.seek_random)
            menu.addAction("Reset zoom", tile.reset_zoom)
            menu.addAction("Sync all to this video", lambda: c.sync_to(tile))
            menu.addAction("Replace with random from folder", lambda: c.replace_with_random(tile))
            speed = menu.addMenu("Speed")
            for rate in SPEEDS:
                a = speed.addAction(f"{rate:g}×", lambda r=rate: tile.set_speed(r))
                a.setCheckable(True)
                a.setChecked(abs(tile.entry.rate - rate) < 1e-6)
            volume = menu.addMenu("Volume")
            for v in (0.25, 0.5, 0.75, 1.0, 1.25):
                volume.addAction(f"{int(v * 100)}%", lambda v=v: tile.set_volume(v))
            menu.addAction("Focus" if c.solo is None else "Show all",
                           lambda: c.set_solo(None if c.solo else tile))
            menu.addAction("Copy file path", lambda: _copy(tile.entry.path))
            menu.addAction("Remove", lambda: c.remove_tile(tile))
            menu.addSeparator()
        for sub in (self.file_menu, self.play_menu, self.layout_menu, self.view_menu,
                    self.settings_menu, self.help_menu):
            menu.addMenu(sub)
        menu.exec(event.globalPos())

    # --- open / save --------------------------------------------------------

    def load(self, inputs):
        entries: list[Entry] = []
        for item in inputs:
            found, params = expand_inputs([item])
            is_playlist = Path(str(item)).suffix.lower() in (".gpls", ".m3u", ".m3u8")
            if is_playlist:
                self._remember_playlist(str(item))
                if params:
                    self._playlist_params = params
            else:
                for e in found:
                    e.muted = self.cfg.start_muted
                    e.start_random = self.cfg.start_random
            entries += found
        if not entries:
            self.toast("No videos found")
            return
        self.canvas.add_entries(entries)

    def _remember_playlist(self, path: str):
        if Path(path).suffix.lower() == ".gpls":
            self._playlist_path = path
        self.cfg.add_recent(str(Path(path).resolve()))
        self.cfg.save()

    def _last_dir(self) -> str:
        return self._store.value("last_dir", str(Path.home()))

    def open_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Open videos or playlist", self._last_dir(),
                                                VIDEO_FILTER)
        if files:
            self._store.setValue("last_dir", str(Path(files[0]).parent))
            self.load(files)

    def open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Add folder (includes subfolders)",
                                                  self._last_dir())
        if folder:
            self._store.setValue("last_dir", folder)
            self.load([folder])

    def open_url(self):
        url, ok = QInputDialog.getText(self, "Open URL", "Video URL (http, https, rtsp…):")
        if ok and url.strip():
            self.canvas.add_entries([Entry(url.strip(), muted=self.cfg.start_muted)])

    def open_playlist(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open playlist", self._last_dir(),
                                              PLAYLIST_FILTER)
        if path:
            self.open_playlist_path(path)

    def open_playlist_path(self, path: str):
        if self.canvas.tiles:
            answer = QMessageBox.question(
                self, "Open playlist", "Replace the videos that are open now?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Yes)
            if answer == QMessageBox.Cancel:
                return
            if answer == QMessageBox.Yes:
                self.canvas.clear()
        self.load([path])

    def save_playlist(self, ask=False):
        if not self.canvas.tiles:
            self.toast("Nothing to save")
            return
        path = self._playlist_path
        if ask or not path:
            start = path or str(Path(self._last_dir()) / "mosaic.gpls")
            path, _ = QFileDialog.getSaveFileName(self, "Save playlist", start,
                                                  "GridPlayer playlist (*.gpls)")
            if not path:
                return
            if not path.endswith(".gpls"):
                path += ".gpls"
        self.canvas.snapshot_positions()
        write_gpls(path, Playlist(self.canvas.entries(), self._playlist_params),
                   save_positions=self.cfg.save_positions, save_paused=self.cfg.save_paused)
        self._remember_playlist(path)
        self.toast(f"Saved {Path(path).name}")

    def close_all(self):
        self.canvas.clear()
        self._playlist_path = None
        self._playlist_params = {}

    # --- drag and drop ------------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() or u.toString() for u in event.mimeData().urls()]
        paths = [p for p in paths if Path(p).is_dir() or is_video(p)
                 or Path(p).suffix.lower() in {".gpls", ".m3u", ".m3u8"} or "://" in p]
        if paths:
            self.load(paths)

    # --- settings -----------------------------------------------------------

    def open_settings(self):
        dialog = SettingsDialog(self.cfg, self)
        if dialog.exec() != QDialog.Accepted or dialog.result_config is None:
            return
        new = dialog.result_config
        reload = any(getattr(new, f) != getattr(self.cfg, f) for f in RELOAD_FIELDS)
        self._replace_config(new)
        if reload and self.canvas.tiles:
            self.toast("Reloading videos with the new decoder settings…")
            self.canvas.reload_all()

    def _replace_config(self, new: Config):
        for name, value in vars(new).items():
            setattr(self.cfg, name, value)
        self.cfg.save()
        self.mode_actions[self.cfg.layout_mode].setChecked(True)
        self.end_actions[self.cfg.end_action].setChecked(True)
        self.crop_action.setChecked(self.cfg.max_crop > 0)
        self.balance_actions[self.cfg.size_balance].setChecked(True)
        self.canvas.set_all_end_action(self.cfg.end_action)
        self._apply_window_config()
        self.canvas.schedule_relayout()
        self.canvas.update()

    def _apply_window_config(self):
        self.menuBar().setVisible(self.cfg.show_menubar and not self.isFullScreen())
        self.menubar_action.setChecked(self.cfg.show_menubar)
        on_top = bool(self.windowFlags() & Qt.WindowStaysOnTopHint)
        if on_top != self.cfg.stay_on_top:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, self.cfg.stay_on_top)
            if self.isVisible():
                self.show()
        self.top_action.setChecked(self.cfg.stay_on_top)

    def set_layout_mode(self, mode: str):
        self.cfg.layout_mode = mode
        self.cfg.save()
        self.canvas.schedule_relayout()
        self.toast(LAYOUT_MODES[mode])

    def set_balance(self, balance: str):
        self.cfg.size_balance = balance
        self.cfg.save()
        self.balance_actions[balance].setChecked(True)
        if self.cfg.layout_mode != "puzzle":
            self.mode_actions["puzzle"].setChecked(True)
            self.cfg.layout_mode = "puzzle"
        self.canvas.schedule_relayout()
        self.toast(BALANCE_MODES[balance])

    def cycle_balance(self):
        keys = list(BALANCE_MODES)
        self.set_balance(keys[(keys.index(self.cfg.size_balance) + 1) % len(keys)])

    def ask_fixed_grid(self):
        cols, ok = QInputDialog.getInt(self, "Fixed grid", "Columns (0 = automatic):",
                                       self.cfg.fixed_cols, 0, 20)
        if not ok:
            return
        rows, ok = QInputDialog.getInt(self, "Fixed grid", "Rows (0 = automatic):",
                                       self.cfg.fixed_rows, 0, 20)
        if not ok:
            return
        self.cfg.fixed_cols, self.cfg.fixed_rows = cols, rows
        self.mode_actions["fixed-grid"].setChecked(True)
        self.set_layout_mode("fixed-grid")

    def set_crop(self, on: bool):
        self.cfg.max_crop = 0.15 if on else 0.0
        self.cfg.save()
        self.canvas.schedule_relayout()
        self.toast("Cropping up to 15% to fill gaps" if on else "No cropping (may leave gaps)")

    def set_gap(self, gap: int):
        self.cfg.gap = max(0, min(gap, 40))
        self.cfg.save()
        self.canvas.schedule_relayout()

    def _set_end_action(self, action: str):
        self.canvas.set_all_end_action(action)
        self.cfg.save()

    def _set_titles(self, mode: str):
        self.cfg.titles = mode
        self.cfg.save()
        self.canvas.update()

    def set_stay_on_top(self, on: bool):
        self.cfg.stay_on_top = on
        self.cfg.save()
        self._apply_window_config()

    def set_menubar(self, on: bool):
        self.cfg.show_menubar = on
        self.cfg.save()
        self._apply_window_config()
        if not on:
            self.toast("Menu bar hidden: right-click or Ctrl+M brings it back")

    def set_hud(self, on: bool):
        self.canvas.show_hud = on
        self.canvas.update()

    # --- misc ---------------------------------------------------------------

    def toggle_mute_all(self):
        tiles = self.canvas.tiles
        self.canvas.set_all_muted(any(not t.entry.muted for t in tiles))

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        self._apply_window_config()

    def escape(self):
        if self.canvas.solo:
            self.canvas.set_solo(None)
        elif self.isFullScreen():
            self.toggle_fullscreen()

    def show_help(self):
        QMessageBox.information(self, "Keyboard and mouse", HELP)

    def show_about(self):
        QMessageBox.about(
            self, "About GridMosaic",
            f"<h3>GridMosaic {__version__}</h3>"
            "<p>Play many videos at once, packed to fill the screen.</p>"
            "<p>Built with mpv and Qt. Reads and writes GridPlayer playlists.</p>")

    def toast(self, text: str, ms: int = 3500):
        self._toast.setText(text)
        self._toast.adjustSize()
        self._toast.move((self.width() - self._toast.width()) // 2,
                         self.height() - self._toast.height() - 40)
        self._toast.show()
        self._toast.raise_()
        self._toast_timer.start(ms)

    def _update_title(self):
        n = len(self.canvas.tiles)
        name = f" — {Path(self._playlist_path).stem}" if self._playlist_path else ""
        self.setWindowTitle(f"GridMosaic{name} — {n} video{'s' * (n != 1)}" if n else "GridMosaic")
        self._empty.setVisible(n == 0)

    def changeEvent(self, event):
        if event.type() == QEvent.WindowStateChange and self.cfg.pause_when_minimized:
            if self.isMinimized() and not self.canvas.all_paused():
                self._paused_by_minimize = True
                for t in self.canvas.tiles:
                    t.player.pause = True  # keep each tile's own paused state
            elif not self.isMinimized() and self._paused_by_minimize:
                self._paused_by_minimize = False
                for t in self.canvas.tiles:
                    t.player.pause = t.entry.paused
        super().changeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._empty.resize(self.canvas.size())

    def closeEvent(self, event):
        self._store.setValue("geometry", self.saveGeometry())
        self.cfg.save()
        self.canvas.shutdown()
        super().closeEvent(event)


def _copy(text: str):
    from PySide6.QtWidgets import QApplication
    QApplication.clipboard().setText(text)
