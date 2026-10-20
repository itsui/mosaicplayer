# Third-party software in MosaicPlayer downloads

The Windows and Linux downloads bundle these components. Each keeps its own
license; their source code is available at the links below.

| Component | License | Source |
|---|---|---|
| [mpv / libmpv](https://mpv.io) | GPL-2.0-or-later | https://github.com/mpv-player/mpv |
| FFmpeg (inside libmpv) | GPL-2.0-or-later / LGPL-2.1-or-later | https://ffmpeg.org/download.html |
| [Qt 6](https://www.qt.io) | LGPL-3.0 | https://download.qt.io/official_releases/qt/ |
| [PySide6](https://doc.qt.io/qtforpython-6/) | LGPL-3.0 | https://code.qt.io/cgit/pyside/pyside-setup.git/ |
| [python-mpv](https://github.com/jaseg/python-mpv) | GPL-2.0-or-later or LGPL-2.1-or-later | https://github.com/jaseg/python-mpv |
| [Python](https://www.python.org) | PSF-2.0 | https://www.python.org/downloads/source/ |
| Windows libmpv build | as mpv | https://github.com/shinchiro/mpv-winbuild-cmake |
| Linux libraries bundled from Ubuntu 24.04 (libmpv's dependencies) | their own licenses, see the Ubuntu packages | https://packages.ubuntu.com/noble/ |

Qt and PySide6 are dynamically linked, so you can replace them with your own
builds (on Linux, with `LD_LIBRARY_PATH` or by editing the bundled folder).
MosaicPlayer itself is GPL-3.0-or-later; see LICENSE.
