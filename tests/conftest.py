import pytest
from PySide6.QtCore import QSettings


@pytest.fixture(autouse=True, scope="session")
def isolated_settings(tmp_path_factory):
    """Keep tests away from the real ~/.config/gridmosaic (window size, settings)."""
    folder = str(tmp_path_factory.mktemp("qsettings"))
    for fmt in (QSettings.NativeFormat, QSettings.IniFormat):
        QSettings.setPath(fmt, QSettings.UserScope, folder)
    yield folder
