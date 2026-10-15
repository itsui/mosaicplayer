"""End-to-end smoke test: drives the real window through its menu actions.

Needs a display (X11/Wayland) and ffmpeg; skipped otherwise. Uses tiny
generated clips only.
"""

import locale
import os
import shutil
import subprocess
import time

import pytest
from PySide6.QtCore import Qt

pytestmark = pytest.mark.skipif(
    not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) or not shutil.which("ffmpeg"),
    reason="needs a display and ffmpeg")


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    folder = tmp_path_factory.mktemp("clips")
    for i, size in enumerate(["320x180", "180x320", "320x180", "240x180", "180x320", "320x180"]):
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"testsrc2=size={size}:rate=25:duration=2", "-c:v", "libx264", "-preset",
                        "ultrafast", "-pix_fmt", "yuv420p", str(folder / f"c{i}.mp4")], check=True)
    return folder


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    application = QApplication.instance() or QApplication([])
    locale.setlocale(locale.LC_NUMERIC, "C")
    yield application


def pump(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from mosaicplayer.config import Config
    from mosaicplayer.window import MainWindow
    cfg = Config()
    monkeypatch.setattr(cfg, "save", lambda: None)
    win = MainWindow(cfg)
    win.resize(900, 600)
    win.show()
    yield win
    win.close()
    pump(app, 0.2)


def test_full_session(app, window, clips, tmp_path):
    from mosaicplayer.layout import LAYOUT_MODES
    from mosaicplayer.playlist import read_gpls
    from mosaicplayer.options import END_ACTIONS

    c = window.canvas
    window.load([clips])
    pump(app, 2.5)
    assert len(c.tiles) == 6
    assert all(t.aspect_known for t in c.tiles), "every tile reported its video size"
    assert len(c.rects) == 6

    # Every packing mode lays out all tiles inside the canvas.
    for mode in LAYOUT_MODES:
        window.set_layout_mode(mode)
        pump(app, 0.4)
        assert len(c.rects) == 6, mode
    window.set_layout_mode("rows")
    pump(app, 0.3)

    # Frames are actually rendered and composited.
    assert c.tile_fps() >= 0
    pump(app, 1.0)
    assert sum(t.frames_drawn for t in c.tiles) > 30
    image = c.grabFramebuffer()
    assert not image.isNull()

    # Playback controls.
    c.set_all_paused(True)
    pump(app, 0.3)
    assert c.all_paused()
    c.set_all_paused(False)
    window.toggle_mute_all()
    for tile in c.tiles:
        tile.seek(1)
    c.sync_to(c.tiles[0])
    c.set_all_speed(1.5)
    for action in END_ACTIONS:
        c.set_all_end_action(action)
    c.set_all_end_action("loop")

    # Arrange: swap, shuffle, sort, focus.
    first, second = c.tiles[0], c.tiles[1]
    c.swap(first, second)
    assert c.tiles[0] is second
    c.shuffle_order()
    c.sort_by_name()
    c.set_solo(c.tiles[2])
    pump(app, 0.3)
    assert len(c.rects) == 1
    c.set_solo(None)
    pump(app, 0.3)
    assert len(c.rects) == 6

    # Save and reload the playlist.
    out = tmp_path / "session.gpls"
    window._playlist_path = str(out)
    window.save_playlist()
    saved = read_gpls(out)
    assert len(saved.entries) == 6

    # Remove one, reload players (as after a decoder change), clear.
    c.remove_tile(c.tiles[-1])
    pump(app, 0.3)
    assert len(c.tiles) == 5
    c.reload_all()
    pump(app, 1.5)
    assert len(c.tiles) == 5
    window.close_all()
    pump(app, 0.3)
    assert not c.tiles

    window.load([str(out)])
    pump(app, 1.5)
    assert len(c.tiles) == 6


def test_focus_mode_and_merged_playlists(app, window, clips, tmp_path):
    from mosaicplayer.playlist import Entry, Playlist, write_gpls
    c = window.canvas
    window.load([clips])
    pump(app, 2.5)
    # Pause/play all only touches the focused video; the hidden ones stay paused.
    c.set_solo(c.tiles[0])
    pump(app, 0.3)
    c.set_all_paused(False)
    pump(app, 0.3)
    assert not c.tiles[0].paused
    assert all(t.paused for t in c.tiles[1:])
    c.set_solo(None)
    pump(app, 0.3)
    assert not any(t.paused for t in c.tiles)
    # Adding a playlist to videos already open must not make it the save target.
    extra = tmp_path / "extra.gpls"
    write_gpls(extra, Playlist([Entry(str(next(clips.iterdir())))]))
    window.load([str(extra)])
    pump(app, 0.5)
    assert window._playlist_path is None


def test_removed_tile_is_gone_at_once(app, window, clips):
    c = window.canvas
    window.load([clips])
    pump(app, 2.5)
    victim = c.tiles[2]
    rect = c._logical(c.rects[c._layout_tiles.index(victim)])
    c.remove_tile(victim)
    assert victim not in c._layout_tiles  # not drawn or clickable, even before relayout
    assert c.tile_at(rect.center()) is not victim
    assert len(c._layout_tiles) == len(c.rects) == 5


def test_stop_at_end_then_play_restarts(app, window, clips):
    c = window.canvas
    window.load([next(clips.iterdir())])
    pump(app, 2.0)
    tile = c.tiles[0]
    c.set_all_end_action("stop")
    tile.seek_to(1.9, exact=True)
    pump(app, 1.5)
    assert tile._at_end and tile.paused
    tile.set_paused(False)
    pump(app, 0.6)
    pos, _ = tile.progress()
    assert not tile.paused and pos < 1.5


def test_closed_tiles_are_freed(app, window, clips):
    import gc
    from PySide6.QtCore import QCoreApplication, QEvent
    from mosaicplayer.tile import Tile
    c = window.canvas
    for _ in range(3):
        window.load([clips])
        pump(app, 1.5)
        window.close_all()
        pump(app, 0.3)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()
    assert not c.findChildren(Tile)
    assert sum(isinstance(o, Tile) for o in gc.get_objects()) <= 6  # not 18


def test_wheel_acts_once_per_notch(app, window, clips):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QWheelEvent
    c = window.canvas
    window.load([clips])
    pump(app, 2.5)
    tile = c._layout_tiles[0]
    pos = c._logical(c.rects[0]).center()

    def wheel(delta, mods=Qt.NoModifier):
        app.sendEvent(c, QWheelEvent(pos, c.mapToGlobal(pos), QPoint(0, 0), QPoint(0, delta),
                                     Qt.NoButton, mods, Qt.NoScrollPhase, False))

    for _ in range(8):  # one notch from a touchpad / high-resolution wheel
        wheel(15, Qt.AltModifier)
    assert tile.entry.size_weight == 1.41  # one step, not eight
    wheel(-120, Qt.AltModifier)
    assert tile.entry.size_weight == 1.0


def test_keyboard_shortcuts(app, window, clips):
    import time
    from PySide6.QtTest import QTest
    c = window.canvas
    cfg = window.cfg
    window.activateWindow()
    window.load([clips])
    pump(app, 2.5)
    c.setFocus()

    def key(k, mod=Qt.NoModifier):
        # Shortcuts only reach the active window. While someone is using the
        # desktop, the window manager may keep focus elsewhere: skip, don't fail.
        deadline = time.monotonic() + 3
        while app.activeWindow() is not window and time.monotonic() < deadline:
            window.activateWindow()
            pump(app, 0.05)
        if app.activeWindow() is not window:
            pytest.skip("the window manager did not give the test window keyboard focus")
        QTest.keyClick(c, k, mod)
        pump(app, 0.2)

    key(Qt.Key_Space)
    assert c.all_paused()
    key(Qt.Key_Space)
    for i, mode in enumerate(["puzzle", "rows", "columns", "grid", "fixed-grid"], 1):
        key(getattr(Qt, f"Key_{i}"))
        assert cfg.layout_mode == mode
    key(Qt.Key_1)
    c.hovered = tile = c._layout_tiles[0]
    key(Qt.Key_Plus)
    assert tile.entry.size_weight == 1.41
    key(Qt.Key_0)
    assert tile.entry.size_weight == 1.0
    gap = cfg.gap
    key(Qt.Key_BracketRight)
    assert cfg.gap == gap + 2
    key(Qt.Key_M, Qt.ControlModifier)
    assert not window.menuBar().isVisible()
    key(Qt.Key_M, Qt.ControlModifier)
    assert window.menuBar().isVisible()
    # Stay on top recreates the native window; it must get keyboard focus back.
    key(Qt.Key_T)
    deadline = time.monotonic() + 5
    while app.activeWindow() is not window and time.monotonic() < deadline:
        pump(app, 0.05)
    assert cfg.stay_on_top
    key(Qt.Key_Space)
    assert c.all_paused()
    key(Qt.Key_T)


def test_toasts_show_file_names_literally(app, window):
    window.toast("<i>clip</i> & co")
    assert window._toast.textFormat() == Qt.PlainText


def test_overlay_timer_idles(app, window, clips):
    c = window.canvas
    window.load([clips])
    pump(app, 2.0)
    c.hovered = None
    c._sync_overlay_timer()
    assert not c._overlay_timer.isActive()
    c.set_hud(True)
    assert c._overlay_timer.isActive()
    c.set_hud(False)
    assert not c._overlay_timer.isActive()


def test_many_videos_asks_first(app, window, clips, monkeypatch):
    from mosaicplayer.playlist import Entry
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "exec", lambda self: None)  # dialog "cancelled"
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: None)
    entries = [Entry(str(p)) for p in sorted(clips.iterdir())] * 12  # 72 videos
    assert window._confirm_many(entries) == []
    assert window._confirm_many(entries[:10]) == entries[:10]


def test_settings_dialog_round_trip(app, window):
    from dataclasses import replace
    from mosaicplayer.settings_dialog import SettingsDialog
    dialog = SettingsDialog(window.cfg, window)
    dialog.gap.setValue(6)
    dialog.layout_mode.setCurrentIndex(dialog.layout_mode.findData("columns"))
    dialog._accept()
    assert dialog.result_config.gap == 6
    assert dialog.result_config.layout_mode == "columns"
    window._replace_config(replace(dialog.result_config, titles="always"))
    assert window.cfg.layout_mode == "columns"
    # Menus show the new values.
    assert window.mode_actions["columns"].isChecked()
    assert window.title_actions["always"].isChecked()


def test_crop_toggle_keeps_custom_amount(app, window):
    window.cfg.max_crop = 0.25  # set in Settings
    window.set_crop(False)
    assert window.cfg.max_crop == 0.0
    window.set_crop(True)
    assert window.cfg.max_crop == 0.25


def test_fullscreen_returns_to_maximized(app, window):
    def wait_for(condition, seconds=4):
        end = time.monotonic() + seconds
        while not condition() and time.monotonic() < end:
            pump(app, 0.05)
        return condition()

    window.showMaximized()
    if not wait_for(window.isMaximized):
        pytest.skip("window manager did not maximize")
    window.toggle_fullscreen()
    assert wait_for(window.isFullScreen)
    window.toggle_fullscreen()
    assert wait_for(lambda: window.isMaximized() and not window.isFullScreen())


def test_settings_restore_defaults_waits_for_ok(app, window):
    from dataclasses import fields, replace
    from mosaicplayer.config import Config
    from mosaicplayer.settings_dialog import SettingsDialog
    changed = replace(window.cfg, gap=12, max_crop=0.35, decoder_mode="cpu", start_muted=True,
                      background="#123456", recent_files=["a.gpls", "b.gpls"], recent_limit=5)
    dialog = SettingsDialog(changed, window)
    assert dialog.max_crop.value() == 35  # not clamped to a lower maximum
    dialog._defaults()
    assert dialog.result_config is None  # nothing applied until OK
    dialog._accept()
    defaults = Config()
    for f in fields(Config):
        expected = changed.recent_files if f.name == "recent_files" else getattr(defaults, f.name)
        assert getattr(dialog.result_config, f.name) == expected, f.name


def test_settings_trims_recent_playlists(app, window):
    from dataclasses import replace
    from mosaicplayer.settings_dialog import SettingsDialog
    dialog = SettingsDialog(replace(window.cfg, recent_files=list("abcdef")), window)
    dialog.recent_limit.setValue(2)
    dialog._accept()
    assert dialog.result_config.recent_files == ["a", "b"]


def test_saving_at_once_keeps_saved_positions(app, window, clips, tmp_path):
    from mosaicplayer.playlist import Entry, Playlist, read_gpls, write_gpls
    source = tmp_path / "positions.gpls"
    paths = sorted(str(p) for p in clips.iterdir())[:3]
    write_gpls(source, Playlist([Entry(p, position=1.25) for p in paths]))
    window.load([str(source)])
    window.save_playlist()  # before the videos have loaded
    assert [e.position for e in read_gpls(source).entries] == [1.25] * 3


def test_adding_some_at_random_keeps_playlist_order(app, window, clips, monkeypatch):
    from mosaicplayer.playlist import Entry
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "exec", lambda self: None)
    some = lambda self: next(b for b in self.buttons() if b.text().startswith("Add 64"))
    monkeypatch.setattr(QMessageBox, "clickedButton", some)
    entries = [Entry(f"/v/{i}.mp4") for i in range(100)]
    chosen = window._confirm_many(entries)
    assert len(chosen) == 64
    assert [int(e.path[3:-4]) for e in chosen] == sorted(int(e.path[3:-4]) for e in chosen)


def test_layout_in_flight_cannot_bring_back_closed_tiles(app, window, clips):
    c = window.canvas
    window.load([clips])
    pump(app, 2.5)
    old_tiles, old_result, generation = list(c._layout_tiles), c.layout_info, c._layout_generation
    window.close_all()
    # A refine thread that started before the close delivers its result now.
    c._layout_done.emit(generation, (old_tiles, old_result, None))
    pump(app, 0.05)
    assert not c._layout_tiles and c.tile_at(c.rect().center()) is None


def test_playlists_keep_their_settings(app, window, clips, tmp_path):
    import json
    from mosaicplayer.playlist import Entry, Playlist, write_gpls
    c, cfg = window.canvas, window.cfg
    defaults = cfg.playlist_settings()
    window.load([clips])
    pump(app, 1.0)
    # Changed from the menus and keys: this playlist only.
    window.set_layout_mode("rows")
    window.set_gap(8)
    cfg.max_crop = 0.25
    c.set_all_end_action("next")
    c.set_seed(3)
    out = tmp_path / "with-settings.gpls"
    window._playlist_path = str(out)
    window.save_playlist()
    saved = json.loads(next(ln for ln in out.read_text().splitlines() if ln.startswith("#P:"))[3:])
    assert saved["mosaicplayer"] == {**cfg.playlist_settings(), "seed": 3}
    assert cfg._defaults == defaults  # the defaults didn't change

    window.close_all()
    assert cfg.playlist_settings() == defaults and c.layout_seed == 0
    window.load([str(out)])
    assert (cfg.layout_mode, cfg.gap, cfg.max_crop, cfg.end_action) == ("rows", 8, 0.25, "next")
    assert c.layout_seed == 3
    assert window.mode_actions["rows"].isChecked() and window.crop_action.isChecked()
    pump(app, 1.0)
    assert all(t.end_action == "next" for t in c.tiles)

    # A playlist without settings (say, from GridPlayer) opens with the defaults.
    plain = tmp_path / "plain.gpls"
    write_gpls(plain, Playlist([Entry(str(next(clips.iterdir())))]))
    window.close_all()
    window.load([str(out)])
    window.close_all()
    window.load([str(plain)])
    assert cfg.playlist_settings() == defaults

    # Save as default.
    window.set_layout_mode("columns")
    window.save_settings_as_default()
    assert cfg._defaults["layout_mode"] == "columns" and cfg.is_default()
    window.set_layout_mode("grid")
    window.use_default_settings()
    assert cfg.layout_mode == "columns" and window.mode_actions["columns"].isChecked()
