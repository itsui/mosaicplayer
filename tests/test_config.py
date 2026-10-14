from PySide6.QtCore import QSettings

from gridmosaic.config import Config


def test_damaged_and_unknown_values_fall_back_to_defaults(isolated_settings):
    store = QSettings("gridmosaic", "gridmosaic")
    store.clear()
    store.setValue("gap", "lots")
    store.setValue("layout_mode", "spiral")
    store.setValue("max_crop", "0.9")
    store.setValue("render_threads", "0")
    store.setValue("titles", "always")
    store.setValue("config_version", "x")
    store.sync()
    cfg = Config.load()
    defaults = Config()
    assert cfg.gap == defaults.gap
    assert cfg.layout_mode == defaults.layout_mode
    assert cfg.max_crop == 0.4  # clamped
    assert cfg.render_threads == 1
    assert cfg.titles == "always"  # valid values still load


def test_save_and_load_round_trip(isolated_settings):
    QSettings("gridmosaic", "gridmosaic").clear()
    cfg = Config(gap=6, layout_mode="rows", recent_files=["/a.gpls"], start_muted=False)
    cfg.save()
    again = Config.load()
    assert (again.gap, again.layout_mode, again.recent_files, again.start_muted) == \
        (6, "rows", ["/a.gpls"], False)


def test_ctrl_c_and_sigterm_close_cleanly(tmp_path):
    import os
    import shutil
    import signal
    import subprocess
    import sys
    import time

    import pytest
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) or not shutil.which("ffmpeg"):
        pytest.skip("needs a display and ffmpeg")
    clip = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=25:duration=3", "-pix_fmt", "yuv420p", str(clip)],
                   check=True)
    env = dict(os.environ, GRIDMOSAIC_NO_VSYNC="1", XDG_CONFIG_HOME=str(tmp_path / "config"),
               PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for sig in (signal.SIGINT, signal.SIGTERM):
        proc = subprocess.Popen([sys.executable, "-m", "gridmosaic", str(clip), "--no-save-config"],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        time.sleep(4)
        proc.send_signal(sig)
        out, _ = proc.communicate(timeout=10)
        assert proc.returncode == 0, (sig, out.decode()[-800:])
        assert b"Destroyed while thread" not in out


def test_command_line_overrides_are_not_saved(isolated_settings):
    from gridmosaic.__main__ import apply_overrides
    QSettings("gridmosaic", "gridmosaic").clear()
    Config(layout_mode="puzzle", gap=2).save()
    cfg = Config.load()
    apply_overrides(cfg, {"layout_mode": "rows", "gap": 10})
    assert cfg.layout_mode == "rows" and cfg.gap == 10  # in effect for this run
    cfg.save()
    again = Config.load()
    assert again.layout_mode == "puzzle" and again.gap == 2  # not saved
    cfg.gap = 6  # changed in the app afterwards: that is saved
    cfg.save()
    assert Config.load().gap == 6
    assert Config.load().layout_mode == "puzzle"
