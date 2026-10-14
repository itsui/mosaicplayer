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
