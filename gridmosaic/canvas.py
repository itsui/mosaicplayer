"""The video wall: one OpenGL widget that draws every tile.

All mpv players render into framebuffers that belong to this widget's single
GL context, and the wall is composed with one blit per tile. That avoids
GridPlayer's design of one native window and one GL context per video.
"""

from __future__ import annotations

import os
import random
import threading
import time

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QOpenGLContext, QPainter, QPen
from PySide6.QtOpenGL import QOpenGLTextureBlitter
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from .config import Config
from .layout import (LayoutResult, Rect, fixed_grid, grid_layout, justified_columns,
                     justified_rows, puzzle)
from .playlist import Entry, natural_key, next_in_folder, siblings
from .render import RenderPool
from .options import NEXT, RANDOM
from .tile import DecoderPolicy, Tile

GL_COLOR_BUFFER_BIT = 0x4000

QUICK_BUDGET_S = 0.12  # first puzzle layout, computed on the UI thread
REFINE_PASSES = 4  # background passes allowed to finish a large equal-area search
ANIMATION_S = 0.28  # tiles glide to new places when the layout changes
LAYOUT_CACHE_SIZE = 32
SIZE_STEPS = (0.35, 0.5, 0.71, 1.0, 1.41, 2.0, 2.83, 4.0)  # tile area multipliers (√2 apart)
SIZE_WAIT_S = 2.0  # how long new videos may hold the layout while reporting their size
DRAG_THRESHOLD_PX = 12


class Canvas(QOpenGLWidget):
    tiles_changed = Signal()
    message = Signal(str)
    _layout_done = Signal(int, object)

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.cfg = config
        self.policy = self._make_policy()
        self.tiles: list[Tile] = []
        self.rects: list[Rect] = []
        self._layout_tiles: list[Tile] = []
        self.layout_info: LayoutResult | None = None
        self.solo: Tile | None = None
        self.hovered: Tile | None = None
        self.show_hud = False
        self._seed = 0
        self._layout_generation = 0
        self._frame_times: list[float] = []
        self._press: tuple[QPointF, Tile] | None = None
        self._dragging = False
        self.pool: RenderPool | None = None  # created once the GL context exists
        self._blitter: QOpenGLTextureBlitter | None = None
        self._unassigned: list[Tile] = []
        self._previous_rects: dict[int, Rect] = {}  # tile id -> rect before the change
        self._layout_cache: dict = {}  # finished layouts by videos/window/settings
        self._shown_size = (0, 0)  # canvas size the current layout was made for
        self._refine_token = 0  # bumped per background search; older ones stop
        self._overlay_image: QImage | None = None  # cached static overlays
        self._overlay_key = None
        self._wheel_key = None  # (tile, action) the wheel deltas below belong to
        self._wheel_accum = 0
        self._wheel_time = 0.0
        self._animation_start = 0.0

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._relayout_timer = QTimer(self, singleShot=True, interval=120)
        self._relayout_timer.timeout.connect(self._relayout_now)
        self._layout_done.connect(self._apply_layout)

        # Progress bars and the stats overlay need repainting even when videos are
        # paused; the timer only runs while one of them is on screen.
        self._overlay_timer = QTimer(self, interval=250)
        self._overlay_timer.timeout.connect(self.update)

        self._cursor_timer = QTimer(self, singleShot=True)
        self._cursor_timer.timeout.connect(self._hide_cursor)

    def _make_policy(self) -> DecoderPolicy:
        c = self.cfg
        return DecoderPolicy(c.decoder_mode, c.gpu_budget_mpx, c.max_gpu_tiles,
                             c.auto_threshold_mpx, c.hwdec_api)

    # --- tile management ----------------------------------------------------

    def add_entries(self, entries: list[Entry]):
        added = 0
        for entry in entries:
            if "://" not in entry.path and not os.path.isfile(entry.path):
                continue
            self._add_tile(entry)
            added += 1
        missing = len(entries) - added
        if missing:
            self.message.emit(f"Skipped {missing} missing file{'s' * (missing > 1)}")
        self.schedule_relayout()
        self.tiles_changed.emit()

    def _add_tile(self, entry: Entry) -> Tile:
        tile = Tile(entry, self.policy, self.cfg.end_action, self.cfg.scaling, self)
        tile.aspect_changed.connect(lambda t=tile: self._on_aspect(t))
        tile.failed.connect(self.update)
        tile.reached_end.connect(lambda t=tile: self._on_reached_end(t))
        if self.pool is not None:
            self.pool.add(tile)
        else:
            self._unassigned.append(tile)
        self.tiles.append(tile)
        return tile

    def _destroy_tile(self, tile: Tile):
        if self.pool is not None:
            self.pool.remove(tile)  # waits until its GL objects are freed
        if tile in self._unassigned:
            self._unassigned.remove(tile)
        tile.close()
        # The canvas is the tile's Qt parent, which would keep it (and its
        # player wrapper) alive forever; detach and delete it.
        for signal in (tile.aspect_changed, tile.failed, tile.reached_end):
            signal.disconnect()
        tile.setParent(None)
        tile.deleteLater()
        if self.hovered is tile:
            self.hovered = None

    def remove_tile(self, tile: Tile):
        if tile not in self.tiles:
            return
        if self.solo is tile:
            self.set_solo(None)
        self._destroy_tile(tile)
        self.tiles.remove(tile)
        self._forget_from_layout({id(tile)})
        self.hovered = None
        self.schedule_relayout()
        self.tiles_changed.emit()

    def clear(self):
        self.set_solo(None)
        for tile in self.tiles:
            self._destroy_tile(tile)
        self._forget_from_layout({id(t) for t in self.tiles})
        self.tiles.clear()
        self.hovered = None
        self.schedule_relayout()
        self.tiles_changed.emit()

    def _forget_from_layout(self, ids: set[int]):
        """Drop closed tiles from what's drawn and clickable until the next layout."""
        kept = [(t, r) for t, r in zip(self._layout_tiles, self.rects) if id(t) not in ids]
        self._layout_tiles = [t for t, _ in kept]
        self.rects = [r for _, r in kept]
        self.update()

    def reload_all(self):
        """Recreate every player (needed after decoder or scaling changes)."""
        self.snapshot_positions()
        entries = [t.entry for t in self.tiles]
        self.clear()
        self.policy = self._make_policy()
        if self.pool is not None and len(self.pool.workers) != self.cfg.render_threads:
            self.pool.stop()
            self.pool = RenderPool(self.context(), self.cfg.render_threads)
            self.pool.frames_ready.connect(self._on_frames_ready)
        self.add_entries(entries)

    def entries(self) -> list[Entry]:
        return [t.entry for t in self.tiles]

    def snapshot_positions(self):
        for t in self.tiles:
            t.snapshot_position()

    def _on_aspect(self, tile: Tile):
        if tile not in self.tiles:
            return  # closed while the notification was queued
        tile.apply_decoder_policy()
        self.schedule_relayout()

    def _on_reached_end(self, tile: Tile):
        if tile not in self.tiles:
            return
        if tile.end_action == NEXT:
            following = next_in_folder(tile.entry.path)
            if following:
                tile.replace(following)
        elif tile.end_action == RANDOM:
            self.replace_with_random(tile, quiet=True)

    # --- layout -------------------------------------------------------------

    def schedule_relayout(self, reshuffle=False):
        if reshuffle:
            self._seed += 1
        self._relayout_timer.start()

    def _visible_tiles(self) -> list[Tile]:
        return [self.solo] if self.solo else list(self.tiles)

    def _layout_key(self, tiles, w, h):
        c = self.cfg
        return (c.layout_mode if len(tiles) > 1 else "single",
                tuple(id(t) for t in tiles), tuple(round(t.aspect, 3) for t in tiles),
                tuple(t.entry.size_weight for t in tiles), round(w), round(h), c.gap,
                round(c.max_crop, 3), c.size_balance, self._seed, c.fixed_cols, c.fixed_rows)

    def _relayout_now(self):
        tiles = self._visible_tiles()
        # Videos report their size a moment after opening. Wait for them (up to
        # SIZE_WAIT_S) instead of reshuffling once per video while they load.
        now = time.monotonic()
        if any(not t.aspect_known and not t.error and now - t.created < SIZE_WAIT_S
               for t in tiles):
            self._relayout_timer.start(150)
            return
        self._layout_generation += 1
        generation = self._layout_generation
        if not tiles:
            self._apply_layout(generation, (tiles, LayoutResult([], 0, 1, 0), None))
            return
        dpr = self.devicePixelRatioF()
        w, h = self.width() * dpr, self.height() * dpr
        if w < 2 or h < 2:
            return  # minimised or not shown yet; lay out when there's room
        key = self._layout_key(tiles, w, h)
        cached = self._layout_cache.get(key)
        if cached is not None:
            # Same videos, window and settings as a layout we already made
            # (e.g. coming back from focusing one video): reuse it exactly.
            self._apply_layout(generation, (tiles, cached, key))
            return
        aspects = [t.aspect for t in tiles]
        weights = [t.entry.size_weight for t in tiles]
        c = self.cfg
        gap = c.gap * dpr
        mode = "grid" if len(tiles) == 1 else c.layout_mode
        if mode == "grid":
            result = grid_layout(aspects, w, h, gap)
        elif mode == "fixed-grid":
            result = fixed_grid(aspects, w, h, c.fixed_cols, c.fixed_rows, gap)
        elif mode == "rows":
            result = justified_rows(aspects, w, h, gap=gap, max_crop=c.max_crop)
        elif mode == "columns":
            result = justified_columns(aspects, w, h, gap=gap, max_crop=c.max_crop)
        else:
            args = dict(gap=gap, max_crop=c.max_crop, balance=c.size_balance, seed=self._seed,
                        weights=weights)
            same_videos = (set(map(id, tiles)) == set(map(id, self._layout_tiles))
                           and self._shown_size == (round(w), round(h)))
            if not same_videos:
                # New or removed videos, or a new window size: tiles need a place
                # right away, so show a quick layout until the refined one is ready.
                self._apply_layout(generation, (tiles, puzzle(
                    aspects, w, h, time_budget=QUICK_BUDGET_S, quick=True, **args), None))
            # Otherwise (a size or setting change) keep the current layout on
            # screen and glide to the refined one when it's done.

            def emit(result):
                if result is None:
                    return  # superseded by a newer search
                try:
                    self._layout_done.emit(generation, (tiles, result, key))
                except RuntimeError:
                    pass  # window closed while the layout was being refined

            # Only one background search at a time: a newer one (say, while the
            # window is being resized) makes the older stop. Focusing a single
            # video doesn't start a search, so the full layout still finishes
            # and is cached for when you come back.
            self._refine_token += 1
            token = self._refine_token

            def refine():
                # Show the first good result at once; deeper passes may improve it.
                for attempt in range(REFINE_PASSES):
                    result = puzzle(aspects, w, h, time_budget=c.layout_effort, on_progress=emit,
                                    abort=lambda: self._refine_token != token, **args)
                    if result is None:
                        return  # superseded
                    if (result.method == "equal" or c.size_balance == "fill"
                            or attempt == REFINE_PASSES - 1):
                        emit(result)
                        return
                    # The equal-area search ran out of time. Its memo keeps the work
                    # done so far, so another pass finishes it instead of settling
                    # for the uneven fallback layout.

            threading.Thread(target=refine, daemon=True).start()
            return
        self._apply_layout(generation, (tiles, result, key))

    def _apply_layout(self, generation: int, payload):
        tiles, result, key = payload
        if key is not None:
            self._remember_layout(key, result)
        if generation != self._layout_generation:
            return  # a newer layout is on its way
        dpr = self.devicePixelRatioF()
        self._shown_size = (round(self.width() * dpr), round(self.height() * dpr))
        old = {id(t): self._shown_rect(t, r) for t, r in zip(self._layout_tiles, self.rects)}
        self._previous_rects = old
        self._animation_start = time.perf_counter()
        self._layout_tiles = tiles
        self.rects = result.rects
        self.layout_info = result
        self.update()

    def _on_frames_ready(self):
        if self.pool is None:
            return  # shutting down
        self.pool.acknowledge()
        self.update()

    def _remember_layout(self, key, result: LayoutResult):
        self._layout_cache.pop(key, None)
        self._layout_cache[key] = result
        while len(self._layout_cache) > LAYOUT_CACHE_SIZE:
            self._layout_cache.pop(next(iter(self._layout_cache)))

    def _shown_rect(self, tile: Tile, final: Rect) -> Rect:
        """Where the tile is drawn right now (mid-animation if one is running)."""
        start = self._previous_rects.get(id(tile))
        t = (time.perf_counter() - self._animation_start) / ANIMATION_S
        if start is None or t >= 1.0:
            return final
        e = 1 - (1 - t) ** 3  # ease out
        return Rect(start.x + (final.x - start.x) * e, start.y + (final.y - start.y) * e,
                    start.w + (final.w - start.w) * e, start.h + (final.h - start.h) * e)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.schedule_relayout()

    def event(self, event):
        # Moving to a screen with another scale factor changes the size in device
        # pixels without a resize event; lay out again for the new screen.
        if event.type() == QEvent.DevicePixelRatioChange:
            self.schedule_relayout()
        return super().event(event)

    # --- painting -----------------------------------------------------------

    def initializeGL(self):
        self._blitter = QOpenGLTextureBlitter()
        self._blitter.create()
        self.pool = RenderPool(self.context(), self.cfg.render_threads)
        self.pool.frames_ready.connect(self._on_frames_ready)
        for tile in self._unassigned:
            self.pool.add(tile)
        self._unassigned.clear()

    def paintGL(self):
        p = QPainter(self)
        # Raw GL (clear + texture blits) must be bracketed so QPainter's own
        # GL state is saved and restored around it.
        p.beginNativePainting()
        f = QOpenGLContext.currentContext().functions()
        bg = QColor(self.cfg.background)
        f.glClearColor(bg.redF(), bg.greenF(), bg.blueF(), 1.0)
        f.glClear(GL_COLOR_BUFFER_BIT)
        dpr = self.devicePixelRatioF()
        viewport = QRect(0, 0, int(self.width() * dpr), int(self.height() * dpr))
        f.glViewport(0, 0, viewport.width(), viewport.height())
        blitter = self._blitter
        blitter.bind()
        for tile, rect in zip(self._layout_tiles, self.rects):
            if tile not in self.tiles:
                continue
            # Render at the final size; mid-animation the texture is just scaled.
            tile.request_size(int(round(rect.w)), int(round(rect.h)))
            shown = self._shown_rect(tile, rect)
            with tile.target.lock:
                fbo = tile.target.front
                if fbo is None:
                    continue
                target = QRectF(round(shown.x), round(shown.y), round(shown.w), round(shown.h))
                transform = QOpenGLTextureBlitter.targetTransform(target, viewport)
                blitter.blit(fbo.texture(), transform, QOpenGLTextureBlitter.OriginTopLeft)
        blitter.release()
        p.endNativePainting()
        if time.perf_counter() - self._animation_start < ANIMATION_S:
            self.update()  # keep animating even if no video has a new frame
        self._paint_overlays(p)
        p.end()
        now = time.perf_counter()
        self._frame_times = [t for t in self._frame_times if now - t < 1.0] + [now]

    def tile_fps(self) -> float:
        """Average frames each tile actually showed per second (1 s window)."""
        now = time.perf_counter()
        total = sum(t.frames_drawn for t in self.tiles)
        last_t, last_total = getattr(self, "_fps_mark", (now, total))
        if not hasattr(self, "_fps_mark"):
            self._fps_mark = (now, total)
        if now - last_t < 1.0 or not self.tiles:
            return getattr(self, "_last_fps", 0.0)
        self._fps_mark = (now, total)
        self._last_fps = max(0.0, (total - last_total) / (now - last_t) / len(self.tiles))
        return self._last_fps

    def _logical(self, rect: Rect) -> QRectF:
        dpr = self.devicePixelRatioF()
        return QRectF(rect.x / dpr, rect.y / dpr, rect.w / dpr, rect.h / dpr)

    def _paint_overlays(self, p: QPainter):
        cursor_visible = self.cursor().shape() != Qt.BlankCursor
        hovered = self.hovered if cursor_visible else None
        animating = time.perf_counter() - self._animation_start < ANIMATION_S
        if animating:
            # Tiles are moving: draw everything live so overlays stay on them.
            self._paint_static_overlays(p, None)
        else:
            # Titles, pause/mute icons and error tiles rarely change; drawing them
            # for 100 tiles every frame took ~18 ms. Cache them in one image.
            dpr = self.devicePixelRatioF()
            key = (self.width(), self.height(), dpr, self.cfg.titles, id(hovered),
                   tuple((id(t), r, t.paused, t.entry.muted, t.error, t.title)
                         for t, r in zip(self._layout_tiles, self.rects)))
            if key != self._overlay_key:
                image = QImage(int(self.width() * dpr), int(self.height() * dpr),
                               QImage.Format_ARGB32_Premultiplied)
                image.setDevicePixelRatio(dpr)
                image.fill(Qt.transparent)
                cache_painter = QPainter(image)
                self._paint_static_overlays(cache_painter, hovered)
                cache_painter.end()
                self._overlay_image, self._overlay_key = image, key
            p.drawImage(0, 0, self._overlay_image)
        p.setFont(self._overlay_font())
        p.setRenderHint(QPainter.Antialiasing)
        if hovered is not None and hovered in self._layout_tiles:
            r = self._rect_of(hovered)
            if not hovered.error:
                self._paint_info_bar(p, hovered, r, full=True)
                p.setPen(QPen(QColor(255, 255, 255, 110), 1.5))
                p.setBrush(Qt.NoBrush)
                p.drawRect(r.adjusted(0.75, 0.75, -0.75, -0.75))
        if self._dragging and self._press and self.hovered and self.hovered is not self._press[1]:
            target = self._rect_of(self.hovered)
            if target:
                p.setPen(QPen(QColor(120, 200, 255), 3))
                p.setBrush(QColor(120, 200, 255, 40))
                p.drawRect(target)
        if self.show_hud:
            self._paint_hud(p)

    def _overlay_font(self) -> QFont:
        font = QFont(self.font())
        font.setPointSizeF(9.5)
        return font

    def _paint_static_overlays(self, p: QPainter, skip: Tile | None):
        """Per-tile overlays that only change with state (not with time)."""
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(self._overlay_font())
        for tile, rect in zip(self._layout_tiles, self.rects):
            r = self._logical(self._shown_rect(tile, rect))
            if tile.error:
                p.fillRect(r, QColor(40, 10, 10))
                p.setPen(QColor(255, 140, 140))
                p.drawText(r, Qt.AlignCenter | Qt.TextWordWrap, f"{tile.title}\n{tile.error}")
                continue
            if tile.paused:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, 140))
                p.drawRoundedRect(QRectF(r.right() - 30, r.top() + 8, 22, 22), 4, 4)
                p.setBrush(QColor(255, 255, 255, 220))
                p.drawRect(QRectF(r.right() - 25, r.top() + 13, 4, 12))
                p.drawRect(QRectF(r.right() - 17, r.top() + 13, 4, 12))
            if not tile.entry.muted:
                p.setPen(QColor(120, 220, 255))
                p.drawText(QPointF(r.left() + 8, r.top() + 20), "🔊")
            if self.cfg.titles == "always" and tile is not skip:
                self._paint_info_bar(p, tile, r, full=False)

    def _paint_info_bar(self, p: QPainter, tile: Tile, r: QRectF, full: bool):
        if self.cfg.titles == "never" and not full:
            return
        bar = QRectF(r.left(), r.bottom() - 26, r.width(), 26)
        p.fillRect(bar, QColor(0, 0, 0, 150))
        text = tile.title
        if full:
            pos, dur = tile.progress()
            if dur > 0:
                p.fillRect(QRectF(bar.left(), bar.bottom() - 3, bar.width() * pos / dur, 3),
                           QColor(120, 200, 255))
            decoder = "GPU" if tile.on_gpu else "CPU"
            text = f"{tile.title}   {_fmt(pos)} / {_fmt(dur)}   [{decoder}]"
        p.setPen(QColor(235, 235, 235))
        elided = p.fontMetrics().elidedText(text, Qt.ElideMiddle, int(bar.width() - 16))
        p.drawText(bar.adjusted(8, 0, -8, -3), Qt.AlignVCenter | Qt.AlignLeft, elided)

    def _paint_hud(self, p: QPainter):
        gpu = sum(1 for t in self.tiles if t.on_gpu)
        info = self.layout_info
        lines = [
            f"{len(self.tiles)} videos   {gpu} on GPU   {len(self.tiles) - gpu} on CPU"
            f"   decoder mode: {self.policy.mode}",
            f"GPU decode budget: {self.policy.used_mpx():.0f} / {self.policy.budget_mpx:.0f} Mpx/s"
            f"   GPU tiles {self.policy.gpu_tiles()} / {self.policy.max_gpu_tiles}",
            f"layout: {self.cfg.layout_mode}"
            + (f"   covered {info.coverage * 100:.1f}%   size spread {info.size_spread:.1f}x"
               f"   max crop {info.max_crop * 100:.0f}%" if info and info.rects else ""),
            f"redraws/s: {len(self._frame_times)}   video frames/s per tile: {self.tile_fps():.1f}",
        ]
        box = QRectF(10, 10, 620, 18 * len(lines) + 12)
        p.fillRect(box, QColor(0, 0, 0, 170))
        p.setPen(QColor(200, 255, 200))
        for i, line in enumerate(lines):
            p.drawText(QPointF(box.left() + 10, box.top() + 20 + 18 * i), line)

    # --- input --------------------------------------------------------------

    def tile_at(self, pos: QPointF) -> Tile | None:
        for tile, rect in zip(self._layout_tiles, self.rects):
            if self._logical(self._shown_rect(tile, rect)).contains(pos):
                return tile
        return None

    def _rect_of(self, tile: Tile) -> QRectF | None:
        for t, rect in zip(self._layout_tiles, self.rects):
            if t is tile:
                return self._logical(self._shown_rect(t, rect))
        return None

    def mouseMoveEvent(self, event):
        self.unsetCursor()
        if self.cfg.hide_cursor_ms > 0:
            self._cursor_timer.start(self.cfg.hide_cursor_ms)
        pos = event.position()
        if self._press and not self._dragging:
            if (pos - self._press[0]).manhattanLength() > DRAG_THRESHOLD_PX:
                self._dragging = True
        if self._dragging:
            self.setCursor(Qt.ClosedHandCursor)
        tile = self.tile_at(pos)
        if tile is not self.hovered:
            self.hovered = tile
            self.update()
        self._sync_overlay_timer()

    def leaveEvent(self, event):
        self.hovered = None
        self._sync_overlay_timer()
        self.update()

    def _hide_cursor(self):
        self.setCursor(Qt.BlankCursor)
        self._sync_overlay_timer()
        self.update()

    def set_hud(self, on: bool):
        self.show_hud = on
        self._sync_overlay_timer()
        self.update()

    def _sync_overlay_timer(self):
        needed = self.show_hud or (self.hovered is not None
                                   and self.cursor().shape() != Qt.BlankCursor)
        if needed and not self._overlay_timer.isActive():
            self._overlay_timer.start()
        elif not needed:
            self._overlay_timer.stop()

    def mousePressEvent(self, event):
        tile = self.tile_at(event.position())
        if tile is None:
            return
        if event.button() == Qt.LeftButton:
            self._press = (event.position(), tile)
            self._dragging = False
        elif event.button() == Qt.MiddleButton:
            tile.set_muted(not tile.entry.muted)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or not self._press:
            return
        _, source = self._press
        target = self.tile_at(event.position())
        if self._dragging:
            if target is not None and target is not source:
                self.swap(source, target)
            self.unsetCursor()
        elif target is source:
            source.toggle_pause()
        self._press = None
        self._dragging = False
        self.update()

    def mouseDoubleClickEvent(self, event):
        tile = self.tile_at(event.position())
        if tile is not None and event.button() == Qt.LeftButton:
            self._press = None  # don't treat the second click as a pause toggle
            tile.toggle_pause()  # undo the pause from the first click
            self.set_solo(None if self.solo else tile)

    def wheelEvent(self, event):
        tile = self.tile_at(event.position())
        if tile is None:
            return
        mods = event.modifiers()
        action = ("size" if mods & Qt.AltModifier else "zoom" if mods & Qt.ControlModifier
                  else "seek-long" if mods & Qt.ShiftModifier else "seek")
        # Touchpads and high-resolution wheels send many small deltas per notch
        # (often 15 of the usual 120); act once per full notch, not per event.
        delta = event.angleDelta().y() or event.angleDelta().x()  # Alt may turn it sideways
        now = time.monotonic()
        key = (id(tile), action)
        if self._wheel_key != key or now - self._wheel_time > 0.5:
            self._wheel_key, self._wheel_accum = key, 0
        self._wheel_time = now
        self._wheel_accum += delta
        notches = int(self._wheel_accum / 120)
        if not notches:
            return
        self._wheel_accum -= notches * 120
        if action == "size":
            self.resize_tile(tile, notches)
        elif action == "zoom":
            r = self._rect_of(tile)
            pos = event.position()
            tile.zoom_at(0.25 * notches,
                         (pos.x() - r.left()) / r.width(), (pos.y() - r.top()) / r.height())
        else:
            tile.seek((30 if action == "seek-long" else 5) * notches)

    # --- commands -----------------------------------------------------------

    def swap(self, a: Tile, b: Tile):
        """Swap two videos' places in the playlist order and the layout."""
        if self.cfg.layout_mode == "puzzle":
            # The puzzle picks positions itself, so a swap trades slots in the
            # current arrangement. That only looks right for similar shapes.
            if max(a.aspect, b.aspect) / min(a.aspect, b.aspect) > 1.3:
                self.message.emit("The puzzle can only swap videos of a similar shape. "
                                  "For free arrangement use justified rows (2) or a grid (4, 5).")
                return
            lt = self._layout_tiles
            if a in lt and b in lt:
                ja, jb = lt.index(a), lt.index(b)
                lt[ja], lt[jb] = b, a
            ia, ib = self.tiles.index(a), self.tiles.index(b)
            self.tiles[ia], self.tiles[ib] = b, a
            self.update()
            return
        ia, ib = self.tiles.index(a), self.tiles.index(b)
        self.tiles[ia], self.tiles[ib] = b, a
        self.schedule_relayout()

    def resize_tile(self, tile: Tile, steps: int):
        """Make a video bigger (steps > 0) or smaller in puzzle layouts."""
        current = tile.entry.size_weight
        index = min(range(len(SIZE_STEPS)), key=lambda i: abs(SIZE_STEPS[i] - current))
        index = max(0, min(index + steps, len(SIZE_STEPS) - 1))
        self._set_weight(tile, SIZE_STEPS[index])

    def reset_tile_size(self, tile: Tile):
        self._set_weight(tile, 1.0)

    def reset_all_sizes(self):
        for t in self.tiles:
            t.entry.size_weight = 1.0
        self.schedule_relayout()

    def _set_weight(self, tile: Tile, weight: float):
        if tile.entry.size_weight == weight:
            return
        tile.entry.size_weight = weight
        if self.cfg.layout_mode != "puzzle":
            self.message.emit("Video sizes apply in the puzzle layout (key 1)")
            return
        self.message.emit(f"{tile.title}: size ×{weight:g}")
        self.schedule_relayout()

    def shuffle_order(self):
        random.shuffle(self.tiles)
        self.schedule_relayout(reshuffle=True)

    def sort_by_name(self):
        self.tiles.sort(key=lambda t: natural_key(t.title))
        self.schedule_relayout()

    def set_solo(self, tile: Tile | None):
        if tile is self.solo:
            return
        self.solo = tile
        # Hidden tiles stop decoding while one video is focused.
        self.resume_after_minimize()
        self._relayout_timer.stop()
        self._relayout_now()

    def replace_with_random(self, tile: Tile, quiet=False):
        in_use = {t.entry.path for t in self.tiles}
        choices = [p for p in siblings(tile.entry.path) if p not in in_use]
        if not choices:
            if not quiet:
                self.message.emit("No other videos in that folder")
            return
        tile.replace(random.choice(choices))

    def set_all_paused(self, paused: bool):
        # While one video is focused, the hidden ones stay paused regardless.
        for t in [self.solo] if self.solo else self.tiles:
            t.set_paused(paused)
        self.update()

    def all_paused(self) -> bool:
        if self.solo:
            return self.solo.paused
        return bool(self.tiles) and all(t.paused for t in self.tiles)

    def resume_after_minimize(self):
        """Restore each video's own paused state (hidden ones stay paused in focus mode)."""
        for t in self.tiles:
            t.player.pause = True if (self.solo and t is not self.solo) else t.entry.paused

    def set_all_muted(self, muted: bool):
        for t in self.tiles:
            t.set_muted(muted)
        self.update()

    def restart_all(self):
        for t in self.tiles:
            t.seek_to(0)

    def sync_to(self, source: Tile):
        """Seek every video to the same time as ``source``."""
        pos, _ = source.progress()
        for t in self.tiles:
            if t is not source:
                t.seek_to(pos, exact=True)

    def set_all_speed(self, rate: float):
        for t in self.tiles:
            t.set_speed(rate)

    def set_all_end_action(self, action: str):
        self.cfg.end_action = action
        for t in self.tiles:
            t.set_end_action(action)

    def save_screenshot(self, path: str):
        self.grabFramebuffer().save(path)

    def shutdown(self):
        for tile in self.tiles:
            self._destroy_tile(tile)
        self.tiles.clear()
        if self.pool is not None:
            self.pool.stop()
            self.pool = None


def _fmt(seconds: float) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"
