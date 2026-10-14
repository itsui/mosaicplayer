"""gridmosaic [videos | folders | playlist.gpls ...]"""

import argparse
import locale
import logging
import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication

from .layout import LAYOUT_MODES
from .tile import DECODER_MODES


def main(argv=None):
    parser = argparse.ArgumentParser(prog="gridmosaic", description=__doc__)
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
    # vsync: one composed frame per display refresh. GRIDMOSAIC_NO_VSYNC=1 turns it
    # off for benchmarks: with the monitor off, NVIDIA throttles vsynced swaps to
    # ~1 Hz, which stalls the UI thread and every Qt timer.
    fmt.setSwapInterval(0 if os.environ.get("GRIDMOSAIC_NO_VSYNC") else 1)
    QSurfaceFormat.setDefaultFormat(fmt)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("GridMosaic")
    app.setOrganizationName("gridmosaic")
    locale.setlocale(locale.LC_NUMERIC, "C")  # libmpv requires this; Qt resets it

    from .config import Config  # after QApplication
    from .window import MainWindow

    cfg = Config.load()
    if args.layout:
        cfg.layout_mode = args.layout
    if args.decoder:
        cfg.decoder_mode = args.decoder
    if args.gap is not None:
        cfg.gap = args.gap
    if args.crop is not None:
        cfg.max_crop = max(0.0, min(args.crop, 40.0)) / 100
    if args.no_crop:
        cfg.max_crop = 0.0
    if args.no_save_config:
        cfg.save = lambda: None  # command-line overrides stay temporary

    win = MainWindow(cfg)
    if args.fullscreen or cfg.start_fullscreen:
        win.toggle_fullscreen()
    else:
        win.show()
    if args.inputs:
        win.load(args.inputs)

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
