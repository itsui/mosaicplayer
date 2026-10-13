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

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QOpenGLContext, QPainter, QPen
from PySide6.QtOpenGL import QOpenGLTextureBlitter
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from .config import Config
from .layout import (LayoutResult, Rect, fixed_grid, grid_layout, justified_columns,
                     justified_rows, puzzle_layout)
from .playlist import Entry, siblings
from .render import RenderPool
from .tile import NEXT, RANDOM, DecoderPolicy, Tile

GL_COLOR_BUFFER_BIT = 0x4000

QUICK_BUDGET_S = 0.08  # first puzzle layout, computed on the UI thread
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
        self._gl_ready = False
        self._frame_times: list[float] = []
        self._press: tuple[QPointF, Tile] | None = None
        self._dragging = False
        self.pool: RenderPool | None = None  # created once the GL context exists
        self._blitter: QOpenGLTextureBlitter | None = None
        self._unassigned: list[Tile] = []

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._relayout_timer = QTimer(self, singleShot=True, interval=120)
        self._relayout_timer.timeout.connect(self._relayout_now)
        self._layout_done.connect(self._apply_layout)

        # Overlays (progress bars, HUD) need periodic repaint even when paused.
        self._overlay_timer = QTimer(self, interval=250)
        self._overlay_timer.timeout.connect(self.update)
        self._overlay_timer.start()

        self._cursor_timer = QTimer(self, singleShot=True)
        self._cursor_timer.timeout.connect(lambda: self.setCursor(Qt.BlankCursor))

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

    def remove_tile(self, tile: Tile):
        if tile not in self.tiles:
            return
        if self.solo is tile:
            self.set_solo(None)
        self._destroy_tile(tile)
        self.tiles.remove(tile)
        self.hovered = None
        self.schedule_relayout()
        self.tiles_changed.emit()

    def clear(self):
        self.set_solo(None)
        for tile in self.tiles:
            self._destroy_tile(tile)
        self.tiles.clear()
        self.hovered = None
        self.schedule_relayout()
        self.tiles_changed.emit()

    def reload_all(self):
        """Recreate every player (needed after decoder or scaling changes)."""
        self.snapshot_positions()
        entries = [t.entry for t in self.tiles]
        self.clear()
        self.policy = self._make_policy()
        if self.pool is not None and len(self.pool.workers) != self.cfg.render_threads:
            self.pool.stop()
            self.pool = RenderPool(self.context(), self.cfg.render_threads)
            self.pool.frames_ready.connect(self.update)
        self.add_entries(entries)

    def entries(self) -> list[Entry]:
        return [t.entry for t in self.tiles]

    def snapshot_positions(self):
        for t in self.tiles:
            t.snapshot_position()

    def _on_aspect(self, tile: Tile):
        tile.apply_decoder_policy()
        self.schedule_relayout()

    def _on_reached_end(self, tile: Tile):
        if tile not in self.tiles:
            return
        if tile.end_action == NEXT:
            folder = siblings(tile.entry.path)
            if tile.entry.path in folder:
                tile.replace(folder[(folder.index(tile.entry.path) + 1) % len(folder)])
        elif tile.end_action == RANDOM:
            self.replace_with_random(tile, quiet=True)

    # --- layout -------------------------------------------------------------

    def schedule_relayout(self, reshuffle=False):
        if reshuffle:
            self._seed += 1
        self._relayout_timer.start()

    def _visible_tiles(self) -> list[Tile]:
        return [self.solo] if self.solo else list(self.tiles)

    def _relayout_now(self):
        tiles = self._visible_tiles()
        self._layout_generation += 1
        generation = self._layout_generation
        if not tiles:
            self._apply_layout(generation, (tiles, LayoutResult([], 0, 1, 0)))
            return
        dpr = self.devicePixelRatioF()
        w, h = self.width() * dpr, self.height() * dpr
        aspects = [t.aspect for t in tiles]
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
            args = dict(gap=gap, max_crop=c.max_crop, evenness=c.evenness, seed=self._seed)
            self._apply_layout(generation, (tiles, puzzle_layout(
                aspects, w, h, time_budget=QUICK_BUDGET_S, restarts=1, **args)))

            def refine():
                refined = puzzle_layout(aspects, w, h, time_budget=c.layout_effort, **args)
                self._layout_done.emit(generation, (tiles, refined))

            threading.Thread(target=refine, daemon=True).start()
            return
        self._apply_layout(generation, (tiles, result))

    def _apply_layout(self, generation: int, payload):
        tiles, result = payload
        if generation != self._layout_generation:
            return  # a newer layout is on its way
        self._layout_tiles = tiles
        self.rects = result.rects
        self.layout_info = result
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.schedule_relayout()

    # --- painting -----------------------------------------------------------

    def initializeGL(self):
        self._blitter = QOpenGLTextureBlitter()
        self._blitter.create()
        self.pool = RenderPool(self.context(), self.cfg.render_threads)
        self.pool.frames_ready.connect(self.update)
        for tile in self._unassigned:
            self.pool.add(tile)
        self._unassigned.clear()
        self._gl_ready = True

    def paintGL(self):
        f = QOpenGLContext.currentContext().functions()
        bg = QColor(self.cfg.background)
        f.glClearColor(bg.redF(), bg.greenF(), bg.blueF(), 1.0)
        f.glClear(GL_COLOR_BUFFER_BIT)
        dpr = self.devicePixelRatioF()
        viewport = QRect(0, 0, int(self.width() * dpr), int(self.height() * dpr))
        blitter = self._blitter
        blitter.bind()
        for tile, rect in zip(self._layout_tiles, self.rects):
            if tile not in self.tiles:
                continue
            w, h = int(round(rect.w)), int(round(rect.h))
            tile.request_size(w, h)
            with tile.target.lock:
                fbo = tile.target.front
                if fbo is None:
                    continue
                target = QRectF(round(rect.x), round(rect.y), w, h)
                transform = QOpenGLTextureBlitter.targetTransform(target, viewport)
                blitter.blit(fbo.texture(), transform, QOpenGLTextureBlitter.OriginTopLeft)
        blitter.release()
        self._paint_overlays()
        now = time.perf_counter()
        self._frame_times = [t for t in self._frame_times if now - t < 1.0] + [now]

    def tile_fps(self) -> float:
        """Average frames each tile actually showed per second, since last asked."""
        now = time.perf_counter()
        total = sum(t.frames_drawn for t in self.tiles)
        last_t, last_total = getattr(self, "_fps_mark", (now, total))
        self._fps_mark = (now, total)
        if now - last_t < 0.2 or not self.tiles:
            return getattr(self, "_last_fps", 0.0)
        self._last_fps = (total - last_total) / (now - last_t) / len(self.tiles)
        return self._last_fps

    def _logical(self, rect: Rect) -> QRectF:
        dpr = self.devicePixelRatioF()
        return QRectF(rect.x / dpr, rect.y / dpr, rect.w / dpr, rect.h / dpr)

    def _paint_overlays(self):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        small = QFont(self.font())
        small.setPointSizeF(9.5)
        p.setFont(small)
        cursor_visible = self.cursor().shape() != Qt.BlankCursor
        for tile, rect in zip(self._layout_tiles, self.rects):
            r = self._logical(rect)
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
            hover = tile is self.hovered and cursor_visible
            if hover or self.cfg.titles == "always":
                self._paint_info_bar(p, tile, r, full=hover)
            if hover:
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
        p.end()

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
            if self._logical(rect).contains(pos):
                return tile
        return None

    def _rect_of(self, tile: Tile) -> QRectF | None:
        for t, rect in zip(self._layout_tiles, self.rects):
            if t is tile:
                return self._logical(rect)
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

    def leaveEvent(self, event):
        self.hovered = None
        self.update()

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
        step = 30 if event.modifiers() & Qt.ShiftModifier else 5
        tile.seek(step if event.angleDelta().y() > 0 else -step)

    # --- commands -----------------------------------------------------------

    def swap(self, a: Tile, b: Tile):
        """Swap two videos' places in the playlist order and the layout."""
        if self.cfg.layout_mode == "puzzle":
            # The puzzle picks positions itself, so a swap trades slots in the
            # current arrangement. That only looks right for similar shapes.
            if max(a.aspect, b.aspect) / min(a.aspect, b.aspect) > 1.3:
                self.message.emit("In puzzle mode only similar-shaped videos can swap. "
                                  "Use justified rows or a grid to arrange freely.")
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

    def shuffle_order(self):
        random.shuffle(self.tiles)
        self.schedule_relayout(reshuffle=True)

    def sort_by_name(self):
        self.tiles.sort(key=lambda t: t.title.lower())
        self.schedule_relayout()

    def set_solo(self, tile: Tile | None):
        if tile is self.solo:
            return
        self.solo = tile
        for t in self.tiles:
            if t is not tile:
                # Hidden tiles stop decoding while one video is focused.
                t.player.pause = True if tile else t.entry.paused
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
        for t in self.tiles:
            t.set_paused(paused)
        self.update()

    def all_paused(self) -> bool:
        return bool(self.tiles) and all(t.paused for t in self.tiles)

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
                t.seek_to(pos)

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
