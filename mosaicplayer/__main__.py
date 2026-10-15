"""mosaicplayer [videos | folders | playlist.gpls ...]"""

import argparse
import locale
import logging
import os
import signal
import socket
import sys
from pathlib import Path

from PySide6.QtCore import QSocketNotifier, QTimer
from PySide6.QtGui import QIcon, QSurfaceFormat
from PySide6.QtWidgets import QApplication

from .layout import LAYOUT_MODES
from .options import DECODER_MODES


def limit_malloc_arenas(arenas=4):
    """Cap glibc's per-thread memory pools.

    Every mpv player runs several threads, and glibc gives busy threads their
    own pools that never shrink. Adding and closing 24 videos 22 times grew
    memory to 2.2 GB; with 4 pools it stays near 300 MB, at the same speed.
    Must run before mpv starts its threads.
    """
    if os.environ.get("MALLOC_ARENA_MAX") or not sys.platform.startswith("linux"):
        return
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6")
        libc.mallopt(-8, arenas)  # M_ARENA_MAX
    except (OSError, AttributeError):
        pass  # not glibc


def close_on_signals(app, win):
    """Ctrl+C / SIGTERM close the window normally (saving settings, stopping players).

    Qt's event loop runs in C++, so Python's signal handlers would only run once
    some Python code does: Ctrl+C was ignored and SIGTERM killed the app without
    cleanup. A socket pair wakes the Qt loop when a signal arrives.
    """
    reader, writer = socket.socketpair()
    reader.setblocking(False)
    writer.setblocking(False)
    signal.set_wakeup_fd(writer.fileno())
    notifier = QSocketNotifier(reader.fileno(), QSocketNotifier.Read, app)

    def drain():
        try:
            reader.recv(64)
        except OSError:
            pass
        win.close()

    notifier.activated.connect(drain)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: None)  # handled via the wakeup fd above
    app._signal_sockets = (reader, writer, notifier)  # keep alive


def apply_overrides(cfg, overrides: dict):
    """Apply command-line overrides for this run only.

    Settings are saved whenever they change in the app (and on exit), which
    made a one-off flag like --layout rows the new permanent default. Saving
    now writes the original value back for any override still in effect;
    a setting changed in the app afterwards is saved as usual.
    """
    saved = {name: getattr(cfg, name) for name in overrides}
    for name, value in overrides.items():
        setattr(cfg, name, value)
    save = cfg.save

    def save_without_overrides():
        current = {name: getattr(cfg, name) for name in overrides}
        for name, value in overrides.items():
            if current[name] == value:
                setattr(cfg, name, saved[name])
        try:
            save()
        finally:
            for name, value in current.items():
                setattr(cfg, name, value)

    cfg.save = save_without_overrides


def main(argv=None):
    limit_malloc_arenas()
    parser = argparse.ArgumentParser(prog="mosaicplayer", description=__doc__)
    parser.add_argument("inputs", nargs="*", help="video files, folders or .gpls/.m3u playlists")
    parser.add_argument("--layout", choices=list(LAYOUT_MODES), help="packing mode")
    parser.add_argument("--decoder", choices=list(DECODER_MODES), help="decoder mode")
    parser.add_argument("--gap", type=int, help="pixels between tiles")
    parser.add_argument("--no-crop", action="store_true", help="never crop videos to fill gaps")
    parser.add_argument("--crop", type=float, metavar="PERCENT",
                        help="crop videos up to this percent to fill gaps (default 15)")
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument("--debug", action="store_true")
    # Test hooks
    parser.add_argument("--screenshot", metavar="PNG", help=argparse.SUPPRESS)
    parser.add_argument("--quit-after", type=float, metavar="SECONDS", help=argparse.SUPPRESS)
    parser.add_argument("--no-save-config", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING)

    fmt = QSurfaceFormat()
    # vsync: one composed frame per display refresh. MOSAICPLAYER_NO_VSYNC=1 turns it
    # off for benchmarks: with the monitor off, NVIDIA throttles vsynced swaps to
    # ~1 Hz, which stalls the UI thread and every Qt timer.
    fmt.setSwapInterval(0 if os.environ.get("MOSAICPLAYER_NO_VSYNC") else 1)
    QSurfaceFormat.setDefaultFormat(fmt)

    # The program name becomes the window's X11 class name; with "python -m"
    # it would be "__main__.py".
    app = QApplication(["mosaicplayer"])
    app.setApplicationName("MosaicPlayer")
    app.setOrganizationName("mosaicplayer")
    # Matches mosaicplayer.desktop, so the menu entry and the taskbar button
    # are recognised as the same app (and show its icon).
    app.setDesktopFileName("mosaicplayer")
    icon = QIcon()
    for png in sorted((Path(__file__).parent / "icons").glob("mosaicplayer-*.png")):
        icon.addFile(str(png))
    app.setWindowIcon(icon)
    locale.setlocale(locale.LC_NUMERIC, "C")  # libmpv requires this; Qt resets it

    from .config import Config, import_old_settings  # after QApplication
    try:
        from .window import MainWindow  # loads libmpv
    except OSError as e:
        if "mpv" not in str(e).lower():
            raise
        print("MosaicPlayer needs the mpv library (libmpv), which wasn't found.\n"
              "Install it, e.g. 'sudo apt install libmpv2' (Debian/Ubuntu/Mint) or\n"
              "'sudo dnf install mpv-libs' (Fedora), then start MosaicPlayer again.",
              file=sys.stderr)
        return 1

    import_old_settings()
    cfg = Config.load()
    overrides = {}
    if args.layout:
        overrides["layout_mode"] = args.layout
    if args.decoder:
        overrides["decoder_mode"] = args.decoder
    if args.gap is not None:
        overrides["gap"] = max(0, min(args.gap, 40))
    if args.crop is not None:
        overrides["max_crop"] = max(0.0, min(args.crop, 40.0)) / 100
    if args.no_crop:
        overrides["max_crop"] = 0.0
    apply_overrides(cfg, overrides)
    if args.no_save_config:
        cfg.save = lambda: None

    win = MainWindow(cfg)
    close_on_signals(app, win)
    # However the app quits, stop the players and render threads first.
    app.aboutToQuit.connect(win.canvas.shutdown)
    if args.fullscreen or cfg.start_fullscreen:
        win.toggle_fullscreen()
    else:
        win.show()
    if args.inputs:
        win.load(args.inputs)
        # A playlist's own settings apply on opening; options given on the
        # command line win over them.
        if overrides:
            for name, value in overrides.items():
                setattr(cfg, name, value)
            win._settings_changed()

    if args.quit_after:
        QTimer.singleShot(int(max(args.quit_after - 10, 1) * 1000), win.canvas.tile_fps)

        def finish():
            if args.screenshot and "{n}" not in args.screenshot:
                win.canvas.save_screenshot(args.screenshot)
            c = win.canvas
            gpu = sum(1 for t in c.tiles if t.on_gpu)
            info = c.layout_info
            print(f"tiles={len(c.tiles)} gpu={gpu} redraws_per_s={len(c._frame_times)}"
                  f" tile_fps={c.tile_fps():.1f}"
                  f" coverage={info.coverage if info else 0:.3f}"
                  f" spread={info.size_spread if info else 0:.2f}", flush=True)
            win.close()

        QTimer.singleShot(int(args.quit_after * 1000), finish)
        if args.screenshot and "{n}" in args.screenshot:
            shots = iter(range(10_000))
            timer = QTimer(win, interval=400)
            timer.timeout.connect(
                lambda: win.canvas.save_screenshot(args.screenshot.format(n=next(shots))))
            QTimer.singleShot(3000, timer.start)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
