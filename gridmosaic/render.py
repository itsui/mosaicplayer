"""Render worker threads.

One thread can only issue so many mpv render calls per second (~0.6 ms each),
which capped the wall at ~30 redraws/s with 60 tiles. Each worker owns an
OpenGL context that shares textures with the window's context, renders its
tiles into double-buffered framebuffers, and the window only composites the
finished textures. mpv's render call releases the GIL, so workers run truly
in parallel.
"""

from __future__ import annotations

import logging
import queue
import threading

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QOffscreenSurface, QOpenGLContext
from PySide6.QtOpenGL import QOpenGLFramebufferObject

log = logging.getLogger(__name__)


class TileTarget:
    """Double-buffered render target for one tile (guarded by ``lock``)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.front: QOpenGLFramebufferObject | None = None  # read by the window
        self.back: QOpenGLFramebufferObject | None = None  # written by the worker
        self.size = (0, 0)  # requested by the window
        self.has_frame = False


class RenderWorker(QThread):
    def __init__(self, share: QOpenGLContext, index: int, pool: "RenderPool"):
        super().__init__()
        self.setObjectName(f"render-{index}")
        self.pool = pool
        self._ctx = QOpenGLContext()
        self._ctx.setFormat(share.format())
        self._ctx.setShareContext(share)
        if not self._ctx.create():
            raise RuntimeError("could not create a shared OpenGL context")
        self._surface = QOffscreenSurface()
        self._surface.setFormat(self._ctx.format())
        self._surface.create()
        self._ctx.moveToThread(self)
        self.tiles: list = []
        self._tiles_lock = threading.Lock()
        self._wake = threading.Event()
        self._calls: queue.Queue = queue.Queue()
        self._running = True

    # --- called from the UI thread ------------------------------------------

    def add(self, tile):
        tile.worker = self
        self.call(tile.init_gl, wait=False)
        with self._tiles_lock:
            self.tiles.append(tile)
        self.wake()

    def remove(self, tile):
        with self._tiles_lock:
            if tile in self.tiles:
                self.tiles.remove(tile)
        self.call(lambda: _release(tile))
        tile.worker = None

    def wake(self):
        self._wake.set()

    def call(self, fn, wait=True, timeout=5.0):
        """Run ``fn`` on this thread with the GL context current."""
        done = threading.Event()
        self._calls.put((fn, done))
        self.wake()
        if wait and not done.wait(timeout):
            log.warning("render worker %s did not answer in time", self.objectName())

    def stop(self):
        self._running = False
        self.wake()
        self.wait(5000)

    # --- worker thread -------------------------------------------------------

    def run(self):
        self._ctx.makeCurrent(self._surface)
        f = self._ctx.functions()
        while self._running:
            self._wake.wait(0.1)
            self._wake.clear()
            self._run_calls()
            with self._tiles_lock:
                tiles = list(self.tiles)
            rendered = []
            for tile in tiles:
                try:
                    if tile.render_into_back():
                        rendered.append(tile)
                except Exception:  # keep the worker alive whatever one tile does
                    log.exception("rendering %s failed", tile.title)
            if rendered:
                f.glFinish()  # textures must be complete before the window samples them
                for tile in rendered:
                    tile.swap_buffers()
                self.pool.notify()
        self._run_calls()
        with self._tiles_lock:
            for tile in self.tiles:
                _release(tile)
            self.tiles.clear()
        self._ctx.doneCurrent()

    def _run_calls(self):
        while True:
            try:
                fn, done = self._calls.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except Exception:
                log.exception("render worker call failed")
            finally:
                done.set()


def _release(tile):
    tile.release_gl()


class RenderPool(QObject):
    """Spreads tiles over a few render workers."""

    frames_ready = Signal()

    def __init__(self, share: QOpenGLContext, threads: int):
        super().__init__()
        self._notify_lock = threading.Lock()
        self._notified = False
        self.workers = [RenderWorker(share, i, self) for i in range(max(1, threads))]
        for w in self.workers:
            w.start()

    def notify(self):
        """Tell the window new frames are ready (any thread).

        At most one notification is in flight: every queued signal is an event
        the UI thread must handle before its timers, and one per rendered batch
        (hundreds a second) starved Qt timers to about one tick per second.
        """
        with self._notify_lock:
            if self._notified:
                return
            self._notified = True
        self.frames_ready.emit()

    def acknowledge(self):
        """UI thread: the window is about to repaint with the latest frames."""
        with self._notify_lock:
            self._notified = False

    def add(self, tile):
        min(self.workers, key=lambda w: len(w.tiles)).add(tile)

    def remove(self, tile):
        if tile.worker is not None:
            tile.worker.remove(tile)

    def stop(self):
        for w in self.workers:
            w.stop()
