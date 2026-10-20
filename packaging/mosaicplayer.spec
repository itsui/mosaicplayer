# PyInstaller recipe for Windows and Linux:  pyinstaller packaging/mosaicplayer.spec
#
# libmpv is not a Python module, so it is passed in:
#   Windows: LIBMPV_DLL=path\to\libmpv-2.dll
#   Linux:   LIBMPV_SO=/usr/lib/x86_64-linux-gnu/libmpv.so.2
# PyInstaller then bundles its dependencies (ffmpeg and friends) as well.
import os
import re
import sys

root = os.path.abspath(os.path.join(SPECPATH, ".."))
windows = sys.platform == "win32"

libmpv = os.environ.get("LIBMPV_DLL" if windows else "LIBMPV_SO")
if not libmpv or not os.path.isfile(libmpv):
    raise SystemExit("Set LIBMPV_DLL (Windows) or LIBMPV_SO (Linux) to the libmpv to bundle")

a = Analysis(
    [os.path.join(SPECPATH, "launch.py")],
    pathex=[root],
    binaries=[(libmpv, ".")],
    datas=[(os.path.join(root, "mosaicplayer", "icons"), os.path.join("mosaicplayer", "icons")),
           # The licenses travel with every download.
           (os.path.join(root, "LICENSE"), "."),
           (os.path.join(root, "THIRD-PARTY-NOTICES.md"), ".")],
    excludes=["tkinter", "PySide6.QtTest", "PySide6.QtWebEngineCore", "PySide6.QtQml",
              "PySide6.QtQuick", "PySide6.QtPdf", "PySide6.QtMultimedia", "pytest"],
)

if not windows:
    # Graphics drivers and core desktop libraries must come from the user's
    # system: bundled copies break GPU acceleration or clash with the desktop
    # (the AppImage project's exclude list, abridged).
    system_only = re.compile(
        r"lib(GL|EGL|GLX|GLdispatch|OpenGL|gbm|drm|X11|X11-xcb|xcb.*|Xext|Xrender|Xfixes|"
        r"Xi|Xrandr|Xinerama|Xcursor|Xss|Xv|xkbcommon.*|wayland-.*|va|va-drm|va-x11|"
        r"va-wayland|vdpau|vulkan|cuda|nvidia.*|asound|pulse.*|pipewire.*|jack|"
        r"fontconfig|freetype|dbus-1|udev|systemd|glib-2\.0|gobject-2\.0|gio-2\.0|"
        r"c|m|dl|pthread|rt|stdc\+\+|gcc_s)\.so.*")
    a.binaries = [b for b in a.binaries if not system_only.fullmatch(os.path.basename(b[0]))]

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="MosaicPlayer",
    console=False,
    icon=os.path.join(root, "packaging", "windows", "mosaicplayer.ico") if windows else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="MosaicPlayer")
