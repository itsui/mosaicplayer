"""One video tile: an mpv player that renders into its own framebuffer."""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path

import mpv
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QOpenGLContext
from PySide6.QtOpenGL import QOpenGLFramebufferObject

from .playlist import Entry
from .render import TileTarget

log = logging.getLogger(__name__)

DEFAULT_ASPECT = 16 / 9

# Decoder modes
AUTO, GPU_COPY, GPU_ZERO_COPY, CPU = "auto", "gpu-copy", "gpu-zero-copy", "cpu"
DECODER_MODES = {
    AUTO: "Auto — CPU for small videos, GPU for large ones",
    GPU_COPY: "GPU (NVDEC, copy) — every video on the GPU while the budget lasts",
    GPU_ZERO_COPY: "GPU zero-copy — least CPU, most video memory",
    CPU: "CPU only",
}

# What a tile does when its video ends
LOOP, NEXT, RANDOM, STOP = "loop", "next", "random", "stop"
END_ACTIONS = {
    LOOP: "Loop the video",
    NEXT: "Play the next video in its folder",
    RANDOM: "Play a random video from its folder",
    STOP: "Stop at the end",
}

SCALING = {
    "quality": dict(scale="spline36", dscale="mitchell", cscale="spline36"),
    # Tiles are nearly always smaller than the video; cheap downscaling keeps
    # dozens of tiles (and 4K sources) at full frame rate.
    "balanced": dict(scale="spline36", dscale="bilinear", cscale="bilinear",
                     correct_downscaling=False, linear_downscaling=False),
    "fast": dict(scale="bilinear", dscale="bilinear", cscale="bilinear",
                 correct_downscaling=False, linear_downscaling=False, sigmoid_upscaling=False),
}


def _mpv_log(level, prefix, text):
    log.debug("mpv[%s] %s: %s", level, prefix, text.rstrip())


def _get_proc_address(_ctx, name):
    gl = QOpenGLContext.currentContext()
    return gl.getProcAddress(name) if gl else 0


# Must outlive every render context, so keep a module-level reference.
_PROC_ADDRESS_FN = mpv.MpvGlGetProcAddressFn(_get_proc_address)


class DecoderPolicy:
    """Decides which tiles decode on the GPU (NVDEC) and which on the CPU.

    Measured on an RTX 3090 with 32 clips of 480p-1080p h264:
      * GridPlayer's VLC/VDPAU path pinned NVDEC at 100% and stuttered;
      * NVDEC zero-copy ran at ~45% (it showed green line artifacts until the
        render workers synchronised with glFinish before handing textures over);
      * every GPU tile costs a CUDA context (a few hundred MB of VRAM), and with
        zero-copy so does every tile, because the interop is set up per player;
      * CPU decoding cost ~3 of 24 cores and no VRAM.
    So by default small videos use the CPU and only large ones use the GPU.
    """

    def __init__(self, mode=AUTO, budget_mpx=1200.0, max_gpu_tiles=24, auto_threshold_mpx=70.0,
                 hwdec_api="nvdec"):
        self.mode = mode
        self.budget_mpx = budget_mpx
        self.max_gpu_tiles = max_gpu_tiles
        self.auto_threshold_mpx = auto_threshold_mpx  # 1080p30 is ~62 Mpx/s
        self.hwdec_api = hwdec_api
        self._gpu: dict[int, float] = {}

    @property
    def gpu_hwdec(self) -> str:
        return self.hwdec_api if self.mode == GPU_ZERO_COPY else f"{self.hwdec_api}-copy"

    @property
    def interop(self) -> str:
        # Interop is only needed for zero-copy; creating it costs a CUDA
        # context per tile even when the tile decodes on the CPU.
        return "auto" if self.mode == GPU_ZERO_COPY else "no"

    def used_mpx(self) -> float:
        return sum(self._gpu.values())

    def gpu_tiles(self) -> int:
        return len(self._gpu)

    def decide(self, tile_id: int, load_mpx: float) -> bool:
        """True if this tile should decode on the GPU."""
        self._gpu.pop(tile_id, None)
        if self.mode == CPU:
            return False
        if self.mode == AUTO and load_mpx < self.auto_threshold_mpx:
            return False
        if (self.used_mpx() + load_mpx > self.budget_mpx
                or self.gpu_tiles() >= self.max_gpu_tiles):
            return False
        self._gpu[tile_id] = load_mpx
        return True

    def release(self, tile_id: int) -> None:
        self._gpu.pop(tile_id, None)


class Tile(QObject):
    aspect_changed = Signal()
    failed = Signal()
    reached_end = Signal()

    def __init__(self, entry: Entry, policy: DecoderPolicy, end_action=LOOP,
                 scaling="balanced", parent=None):
        super().__init__(parent)
        self.entry = entry
        self.policy = policy
        self.end_action = end_action
        self.aspect = DEFAULT_ASPECT
        self.aspect_known = False
        self.created = time.monotonic()
        self.error: str | None = None
        self.uses_gpu = False
        self.load_mpx = 0.0
        self._ctx: mpv.MpvRenderContext | None = None
        self.target = TileTarget()
        self.worker = None  # RenderWorker that owns this tile's GL objects
        self.frames_drawn = 0  # for the stats overlay
        self._dirty = True
        self._paused = bool(entry.paused)
        self._hwdec_current = "no"
        self._resume_at = entry.position  # restore a saved playlist position once

        # GPU modes start on the GPU (if there's room) before the size is known;
        # auto/CPU start on the CPU and move up once the video turns out large.
        start_gpu = policy.mode in (GPU_COPY, GPU_ZERO_COPY) and policy.decide(id(self), 30.0)
        self.uses_gpu = start_gpu

        self.player = mpv.MPV(
            vo="libmpv",
            hwdec=policy.gpu_hwdec if start_gpu else "no",
            gpu_hwdec_interop=policy.interop,
            loop_file="inf" if end_action == LOOP else "no",
            keep_open="yes",
            mute="yes" if entry.muted else "no",
            aid="no" if entry.muted else "auto",
            pause=entry.paused,
            speed=entry.rate,
            volume=entry.volume * 100,
            # Fill the tile; the layout keeps any crop small.
            keepaspect="yes",
            panscan=1.0,
            # Many players at once: keep each one lean.
            cache="no",
            demuxer_max_bytes="16MiB",
            demuxer_max_back_bytes="8MiB",
            vd_lavc_threads=2,
            hr_seek="no",
            osc=False,
            input_default_bindings=False,
            input_vo_keyboard=False,
            terminal=False,
            ytdl="://" in entry.path,  # resolve web pages via yt-dlp if installed
            sub_auto="no",
            log_handler=_mpv_log if log.isEnabledFor(logging.DEBUG) else None,
            loglevel="v" if log.isEnabledFor(logging.DEBUG) else "warn",
            **SCALING.get(scaling, SCALING["balanced"]),
        )
        self.player.observe_property("video-params", self._on_video_params)
        self.player.observe_property("pause", self._on_pause)
        self.player.observe_property("hwdec-current", self._on_hwdec)
        self.player.observe_property("eof-reached", self._on_eof)
        self.player.event_callback("end-file")(self._on_end_file)
        self.player.event_callback("file-loaded")(self._on_file_loaded)
        # Playback starts in init_gl(): libmpv's video output fails to open
        # unless the render context already exists.

    # --- mpv callbacks (run on mpv threads; only emit signals here) ---------

    def _on_video_params(self, _name, params):
        if not params or not params.get("aspect"):
            return
        aspect = params["aspect"]
        if not self.aspect_known or abs(aspect - self.aspect) > 1e-3:
            self.aspect = aspect
            self.aspect_known = True
            fps = self.player.container_fps or 30.0
            self.load_mpx = params.get("w", 0) * params.get("h", 0) * fps / 1e6
            self.aspect_changed.emit()

    def _on_pause(self, _name, value):
        self._paused = bool(value)

    def _on_hwdec(self, _name, value):
        self._hwdec_current = value or "no"

    def _on_eof(self, _name, value):
        if value and self.end_action != LOOP:
            self.reached_end.emit()

    def _on_file_loaded(self, _event):
        if self._resume_at:
            self.seek_to(self._resume_at)
            self._resume_at = 0.0
        elif self.entry.start_random:
            self.seek_random()

    def _on_end_file(self, event):
        reason = getattr(event.data, "reason", None)
        if reason == mpv.MpvEventEndFile.ERROR:
            self.error = "cannot play this file"
            self.failed.emit()

    # --- decoder assignment (UI thread) -------------------------------------

    def apply_decoder_policy(self):
        use_gpu = self.policy.decide(id(self), self.load_mpx)
        if use_gpu != self.uses_gpu:
            self.uses_gpu = use_gpu
            self.player.hwdec = self.policy.gpu_hwdec if use_gpu else "no"

    @property
    def hwdec_active(self) -> str:
        return self._hwdec_current

    @property
    def on_gpu(self) -> bool:
        return self._hwdec_current not in ("no", "")

    # --- rendering (render-worker thread, its GL context current) -----------

    def init_gl(self):
        try:
            self._ctx = mpv.MpvRenderContext(
                self.player, "opengl",
                opengl_init_params={"get_proc_address": _PROC_ADDRESS_FN},
            )
        except Exception:
            log.exception("could not create the video output for %s", self.entry.path)
            self.error = "could not start video output"
            self.failed.emit()
            return
        self._ctx.update_cb = self._on_render_update
        if self.player.path is None:
            self.player.play(self.entry.path)

    def _on_render_update(self):
        self._dirty = True
        worker = self.worker
        if worker is not None:
            worker.wake()

    def request_size(self, width: int, height: int):
        """Called by the window when the layout changes the tile's size."""
        if self.target.size != (width, height):
            self.target.size = (width, height)
            self._dirty = True
            if self.worker is not None:
                self.worker.wake()

    def render_into_back(self) -> bool:
        """Render a new frame into the back buffer. True if something was drawn."""
        if self._ctx is None:
            return False
        width, height = self.target.size
        if width < 1 or height < 1:
            return False
        resized = False
        back = self.target.back
        if back is None or back.width() != width or back.height() != height:
            self.target.back = back = QOpenGLFramebufferObject(width, height)
            resized = True
        has_frame = self._ctx.update()
        if not (has_frame or resized or self._dirty):
            return False
        self._dirty = False
        # Don't wait for the frame's display time: with dozens of tiles those
        # waits add up and the whole wall drops frames.
        self._ctx.render(flip_y=False, block_for_target_time=False,
                         opengl_fbo={"w": width, "h": height, "fbo": back.handle()})
        return True

    def swap_buffers(self):
        self.frames_drawn += 1
        with self.target.lock:
            self.target.front, self.target.back = self.target.back, self.target.front
            self.target.has_frame = True
        # The new back buffer still holds an older frame; re-render it next time
        # even if mpv has no new frame, so both buffers converge after a resize.
        self._dirty = self._dirty or self.target.back is None

    def release_gl(self):
        if self._ctx is not None:
            self._ctx.free()
            self._ctx = None
        with self.target.lock:
            self.target.front = self.target.back = None
            self.target.has_frame = False

    # --- controls (UI thread) -----------------------------------------------

    @property
    def title(self) -> str:
        return Path(self.entry.path).stem if "://" not in self.entry.path else self.entry.path

    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool):
        self._paused = paused
        self.player.pause = paused
        self.entry.paused = paused

    def toggle_pause(self):
        self.set_paused(not self.paused)

    def set_muted(self, muted: bool):
        self.entry.muted = muted
        if not muted:
            self.player.aid = "auto"
        self.player.mute = muted
        if muted:
            self.player.aid = "no"  # skip audio decoding entirely

    def set_volume(self, volume: float):
        self.entry.volume = max(0.0, min(volume, 1.5))
        self.player.volume = self.entry.volume * 100

    def set_speed(self, rate: float):
        self.entry.rate = max(0.1, min(rate, 8.0))
        self.player.speed = self.entry.rate

    def set_end_action(self, action: str):
        self.end_action = action
        self.player.loop_file = "inf" if action == LOOP else "no"

    def seek(self, seconds: float):
        try:
            self.player.seek(seconds, "relative")
        except SystemError:
            pass

    def seek_to(self, seconds: float):
        try:
            self.player.seek(max(0.0, seconds), "absolute")
        except SystemError:
            pass

    def seek_random(self):
        duration = self.player.duration
        if duration:
            self.seek_to(random.uniform(0, duration * 0.9))

    def zoom_at(self, steps: float, fx: float, fy: float):
        """Zoom by ``steps`` (log2 units) keeping the point (fx, fy) of the tile still.

        fx, fy are the cursor's position inside the tile, 0..1.
        """
        z = self.player.video_zoom or 0.0
        s = 2.0 ** z
        new_z = max(0.0, min(z + steps, 4.0))
        s2 = 2.0 ** new_z
        if new_z == 0.0:
            self.reset_zoom()
            return
        # mpv maps video point u to screen x = 0.5 + (u - 0.5) * s + pan * s
        for axis, f in (("x", fx), ("y", fy)):
            pan = getattr(self.player, f"video_pan_{axis}") or 0.0
            u = (f - 0.5 - pan * s) / s + 0.5
            new_pan = (f - 0.5 - (u - 0.5) * s2) / s2
            setattr(self.player, f"video_pan_{axis}", new_pan)
        self.player.video_zoom = new_z

    def reset_zoom(self):
        self.player.video_zoom = 0.0
        self.player.video_pan_x = 0.0
        self.player.video_pan_y = 0.0

    def progress(self) -> tuple[float, float]:
        return (self.player.time_pos or 0.0, self.player.duration or 0.0)

    def snapshot_position(self):
        """Copy the current position into the entry before saving a playlist."""
        self.entry.position = self.player.time_pos or 0.0

    def replace(self, path: str):
        self.entry.path = path
        self.error = None
        self.aspect_known = False
        self.created = time.monotonic()
        self.player.play(path)

    def close(self):
        """Free the player. GL resources must already be released by the worker."""
        self.policy.release(id(self))
        self.player.terminate()
