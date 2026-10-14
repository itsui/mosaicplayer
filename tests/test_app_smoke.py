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
    from gridmosaic.config import Config
    from gridmosaic.window import MainWindow
    cfg = Config()
    monkeypatch.setattr(cfg, "save", lambda: None)
    win = MainWindow(cfg)
    win.resize(900, 600)
    win.show()
    yield win
    win.close()
    pump(app, 0.2)


def test_full_session(app, window, clips, tmp_path):
    from gridmosaic.layout import LAYOUT_MODES
    from gridmosaic.playlist import read_gpls
    from gridmosaic.tile import END_ACTIONS

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
    from gridmosaic.playlist import Entry, Playlist, write_gpls
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
    from gridmosaic.tile import Tile
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
    from gridmosaic.playlist import Entry
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "exec", lambda self: None)  # dialog "cancelled"
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: None)
    entries = [Entry(str(p)) for p in sorted(clips.iterdir())] * 12  # 72 videos
    assert window._confirm_many(entries) == []
    assert window._confirm_many(entries[:10]) == entries[:10]


def test_settings_dialog_round_trip(app, window):
    from gridmosaic.settings_dialog import SettingsDialog
    dialog = SettingsDialog(window.cfg, window)
    dialog.gap.setValue(6)
    dialog.layout_mode.setCurrentIndex(dialog.layout_mode.findData("columns"))
    dialog._accept()
    assert dialog.result_config.gap == 6
    assert dialog.result_config.layout_mode == "columns"
    window._replace_config(dialog.result_config)
    assert window.cfg.layout_mode == "columns"
