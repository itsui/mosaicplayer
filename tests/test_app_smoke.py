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
