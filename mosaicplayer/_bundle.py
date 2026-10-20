"""Find the libmpv shipped inside a packaged build (PyInstaller, AppImage).

python-mpv looks libmpv up with ctypes.util.find_library, which only searches
the system: in a packaged build the bundled copy must be pointed out before
``import mpv``.
"""

import ctypes.util
import glob
import os
import sys


def bundle_dir() -> str | None:
    """The folder holding the bundled files, or None when running from source."""
    return getattr(sys, "_MEIPASS", None) if getattr(sys, "frozen", False) else None


def use_bundled_libmpv() -> None:
    base = bundle_dir()
    if base is None:
        return
    if sys.platform == "win32":
        # python-mpv finds mpv-2.dll / libmpv-2.dll through PATH.
        os.environ["PATH"] = base + os.pathsep + os.environ.get("PATH", "")
        os.add_dll_directory(base)
        return
    found = sorted(glob.glob(os.path.join(base, "**", "libmpv.so*"), recursive=True))
    if not found:
        return  # not bundled: use the system's libmpv
    bundled = found[0]
    system_find = ctypes.util.find_library

    def find_library(name):
        return bundled if name == "mpv" else system_find(name)

    ctypes.util.find_library = find_library
