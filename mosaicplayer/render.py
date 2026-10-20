"""Render worker threads.

One thread can only issue so many mpv render calls per second (~0.6 ms each),
which capped the wall at ~30 redraws/s with 60 tiles. Each worker owns an
OpenGL context that shares textures with the window's context, renders its
tiles into double-buffered framebuffers, and the window only composites the
finished textures. mpv's render call releases the GIL, so workers run truly
in parallel.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QOffscreenSurface, QOpenGLContext
from PySide6.QtOpenGL import QOpenGLFramebufferObject

log = logging.getLogger(__name__)


GL_SYNC_GPU_COMMANDS_COMPLETE = 0x9117
GL_TIMEOUT_IGNORED = 0xFFFFFFFFFFFFFFFF


class _SyncFunctions:
    """glFenceSync / glWaitSync / glDeleteSync (PySide6 has no bindings for them)."""

    def __init__(self, ctx: QOpenGLContext):
        def load(name, restype, *argtypes):
            address = int(ctx.getProcAddress(name))
            if not address:
                raise RuntimeError(f"OpenGL function {name.decode()} is not available")
            return ctypes.CFUNCTYPE(restype, *argtypes)(address)

        self.fence = load(b"glFenceSync", ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)
        self.wait = load(b"glWaitSync", None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint64)
        self.delete = load(b"glDeleteSync", None, ctypes.c_void_p)


_gl_sync: _SyncFunctions | None = None
_fence_lock = threading.Lock()


def sync_functions(ctx: QOpenGLContext) -> _SyncFunctions:
    global _gl_sync
    if _gl_sync is None:
        _gl_sync = _SyncFunctions(ctx)
    return _gl_sync


class Fence:
    """A GPU fence placed after the window drew a frame, shared by the tiles it read.

    The window and the workers use different OpenGL contexts, whose commands
    the GPU may run in any order. Without a fence, a worker could start
    rendering the next video frame into a buffer while the GPU was still
    drawing the window from it: mpv clears the buffer first, so tiles
    flickered black (2% of frames with 17 videos at 60 fps).
    """

    def __init__(self, gl: _SyncFunctions):
        self._gl = gl
        self.handle = gl.fence(GL_SYNC_GPU_COMMANDS_COMPLETE, 0)
        self._refs = 0

    def acquire(self) -> "Fence":
        with _fence_lock:
            self._refs += 1
        return self

    def release(self):
        """Drop one reference (needs a current context of the share group)."""
        with _fence_lock:
            self._refs -= 1
            dead = self._refs == 0
        if dead and self.handle:
            self._gl.delete(self.handle)  # deferred by GL while a wait still uses it
            self.handle = None

    def gpu_wait(self):
        """Make this thread's later GPU commands wait for the fence (no CPU wait)."""
        if self.handle:
            self._gl.wait(self.handle, 0, GL_TIMEOUT_IGNORED)


class TileTarget:
    """Double-buffered render target for one tile (guarded by ``lock``)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.front: QOpenGLFramebufferObject | None = None  # read by the window
        self.back: QOpenGLFramebufferObject | None = None  # written by the worker
        self.size = (0, 0)  # requested by the window
        # Set by the window while it draws from this tile, until its fence exists.
        self.reading = False
        # Per buffer (by id): fence after the window last drew from it. Waiting
        # for that one, not the latest frame's, rarely waits at all: by the time
        # a buffer comes back to be rendered into, that frame is usually done.
        self.read_fences: dict[int, Fence] = {}
        # Fence after the worker rendered the current front buffer; the window
        # makes the GPU wait for it before drawing from that buffer.
        self.write_fence: Fence | None = None

    def mark_read(self, fbo, fence: "Fence | None"):
        """Window (holding ``lock``): it drew from ``fbo`` up to ``fence``."""
        old = self.read_fences.pop(id(fbo), None)
        if fence is not None:
            self.read_fences[id(fbo)] = fence.acquire()
        self.reading = False
        return old  # to release outside the lock

    def claim_back(self) -> bool:
        """Worker: make the back buffer safe to render into, or False to retry soon."""
        with self.lock:
            if self.reading:
                return False  # the window is drawing from this tile right now
            back = self.back
            fence = self.read_fences.pop(id(back), None) if back is not None else None
        if fence is not None:
            fence.gpu_wait()
            fence.release()
        return True

    def take_write_fence(self) -> "Fence | None":
        """Window (holding ``lock``): the fence to wait for before drawing the front."""
        fence, self.write_fence = self.write_fence, None
        return fence

    def drop_fences(self):
        with self.lock:
            fences, self.read_fences = list(self.read_fences.values()), {}
            if self.write_fence is not None:
                fences.append(self.write_fence)
                self.write_fence = None
        for fence in fences:
            fence.release()


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
        # Tiles with something to render (a new frame, a resize). Visiting only
        # those, not every tile each time any one has a frame, kept 120 videos
        # from spinning the workers (43,000 visits/s) and starving the window.
        self._pending: set = set()
        self._pending_lock = threading.Lock()
        self._calls: queue.Queue = queue.Queue()
        self._running = True

    # --- called from the UI thread ------------------------------------------

    def add(self, tile):
        tile.worker = self
        self.call(tile.init_gl, wait=False)
        with self._tiles_lock:
            self.tiles.append(tile)
        self.mark(tile)

    def remove(self, tile):
        with self._tiles_lock:
            if tile in self.tiles:
                self.tiles.remove(tile)
        self.call(lambda: _release(tile))
        tile.worker = None

    def wake(self):
        self._wake.set()

    def mark(self, tile):
        """Any thread: ``tile`` has something to render."""
        with self._pending_lock:
            self._pending.add(tile)
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
        gl = sync_functions(self._ctx)
        while self._running:
            self._wake.wait(0.1)
            self._wake.clear()
            self._run_calls()
            with self._pending_lock:
                pending, self._pending = self._pending, set()
            with self._tiles_lock:
                tiles = [t for t in self.tiles if t in pending]
            rendered = []
            deferred = []
            for tile in tiles:
                try:
                    drew = tile.render_into_back()
                    if drew:
                        rendered.append(tile)
                    elif drew is None:
                        deferred.append(tile)  # the window was drawing from it
                except Exception:  # keep the worker alive whatever one tile does
                    log.exception("rendering %s failed", tile.title)
            if rendered:
                # The window must not draw these frames before the GPU finished
                # rendering them. A fence lets its GPU wait for that, instead of
                # this thread blocking in glFinish after every batch.
                fence = Fence(gl)
                f.glFlush()  # other contexts can only wait for a flushed fence
                for tile in rendered:
                    tile.swap_buffers(fence)
                    if tile.needs_render():
                        deferred.append(tile)  # e.g. the other buffer after a resize
                fence.acquire()
                fence.release()  # the batch's own reference: deleted once all are done
                self.pool.notify()
            if deferred:
                time.sleep(0.001)  # the window is mid-frame; try those tiles again
                with self._pending_lock:
                    self._pending.update(deferred)
                self._wake.set()
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
