"""Tile layout engines.

``puzzle_layout`` arranges videos as a slicing tree: every internal node splits
its rectangle either side-by-side ("H") or stacked ("V"), and every leaf is one
video. Because a slicing tree's aspect ratio follows directly from its leaves,
the screen can be tiled with no gaps whenever each tile may be cropped a little
(``max_crop``). A short simulated-annealing search picks the tree that keeps
tile sizes even and cropping small.

``grid_layout`` is the classic equal-cell grid, with the column count chosen to
maximise covered area instead of being set by hand.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass

H, V = "H", "V"


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def area(self) -> float:
        return self.w * self.h


@dataclass
class LayoutResult:
    rects: list[Rect]  # one per input aspect, same order
    coverage: float  # fraction of the screen covered by video
    size_spread: float  # largest / smallest tile area
    max_crop: float  # largest fraction of any video cut off


# A tree is either a leaf index (int) or a tuple (op, left, right).
Tree = int | tuple


def _leaf_range(aspect: float, max_crop: float) -> tuple[float, float]:
    keep = 1.0 - max_crop
    return aspect * keep, aspect / keep


class _Evaluator:
    """Evaluates slicing trees for a fixed set of aspects and screen size."""

    def __init__(self, aspects, width, height, gap, max_crop, evenness, crop_cost,
                 extreme_weight=0.25):
        self.aspects = aspects
        self.width = width
        self.height = height
        self.gap = gap
        self.leaf_ranges = [_leaf_range(a, max_crop) for a in aspects]
        self.evenness = evenness
        self.crop_cost = crop_cost
        self.extreme_weight = extreme_weight

    # Bottom-up: achievable aspect range and leaf count of every subtree.
    def _ranges(self, tree, out):
        if isinstance(tree, int):
            lo, hi = self.leaf_ranges[tree]
            out[id(tree), tree] = (lo, hi, 1)
            return lo, hi, 1
        op, left, right = tree
        lo1, hi1, k1 = self._ranges(left, out)
        lo2, hi2, k2 = self._ranges(right, out)
        if op == H:
            lo, hi = lo1 + lo2, hi1 + hi2
        else:
            lo, hi = lo1 * lo2 / (lo1 + lo2), hi1 * hi2 / (hi1 + hi2)
        out[id(tree), None] = (lo, hi, k1 + k2)
        return lo, hi, k1 + k2

    @staticmethod
    def _key(tree):
        return (id(tree), tree) if isinstance(tree, int) else (id(tree), None)

    def place(self, tree) -> list[Rect]:
        ranges = {}
        lo, hi, _ = self._ranges(tree, ranges)
        screen = self.width / self.height
        aspect = min(max(screen, lo), hi)
        if aspect >= screen:
            w, h = self.width, self.width / aspect
        else:
            w, h = self.height * aspect, self.height
        x0, y0 = (self.width - w) / 2, (self.height - h) / 2
        rects: list[Rect | None] = [None] * len(self.aspects)
        self._place(tree, x0, y0, w, h, ranges, rects)
        return rects  # type: ignore[return-value]

    def _place(self, tree, x, y, w, h, ranges, rects):
        if isinstance(tree, int):
            rects[tree] = Rect(x, y, max(w, 1.0), max(h, 1.0))
            return
        op, left, right = tree
        lo1, hi1, k1 = ranges[self._key(left)]
        lo2, hi2, k2 = ranges[self._key(right)]
        gap = self.gap
        if op == H:
            inner_w = max(w - gap, 1.0)
            r = inner_w / h
            # Ideal: split width by leaf count so leaves get similar areas.
            r1 = r * k1 / (k1 + k2)
            if lo1 + lo2 <= r <= hi1 + hi2:
                r1 = min(max(r1, max(lo1, r - hi2)), min(hi1, r - lo2))
            else:
                r1 = r * (lo1 + hi1) / (lo1 + hi1 + lo2 + hi2)
            w1 = h * r1
            self._place(left, x, y, w1, h, ranges, rects)
            self._place(right, x + w1 + gap, y, inner_w - w1, h, ranges, rects)
        else:
            inner_h = max(h - gap, 1.0)
            u = inner_h / w  # inverse aspect
            u1 = u * k1 / (k1 + k2)
            ulo1, uhi1, ulo2, uhi2 = 1 / hi1, 1 / lo1, 1 / hi2, 1 / lo2
            if ulo1 + ulo2 <= u <= uhi1 + uhi2:
                u1 = min(max(u1, max(ulo1, u - uhi2)), min(uhi1, u - ulo2))
            else:
                u1 = u * (ulo1 + uhi1) / (ulo1 + uhi1 + ulo2 + uhi2)
            h1 = w * u1
            self._place(left, x, y, w, h1, ranges, rects)
            self._place(right, x, y + h1 + gap, w, inner_h - h1, ranges, rects)

    def score(self, tree) -> tuple[float, list[Rect]]:
        rects = self.place(tree)
        return self.score_rects(rects), rects

    def score_rects(self, rects) -> float:
        areas = [r.area for r in rects]
        coverage = sum(areas) / (self.width * self.height)
        logs = [math.log(a) for a in areas]
        mean = sum(logs) / len(logs)
        spread = math.sqrt(sum((v - mean) ** 2 for v in logs) / len(logs))
        extremes = max(logs) - min(logs)  # log of largest/smallest ratio
        crop = sum(_crop(r, a) for r, a in zip(rects, self.aspects)) / len(rects)
        return (coverage - self.evenness * (spread + self.extreme_weight * extremes)
                - self.crop_cost * crop)


def _crop(rect: Rect, aspect: float) -> float:
    shown = rect.w / rect.h
    return 1.0 - min(shown, aspect) / max(shown, aspect)


def _initial_tree(items, aspects, target, rng) -> Tree:
    """Recursive bisection that roughly matches the target aspect."""
    if len(items) == 1:
        return items[0]
    n = len(items)
    k = n // 2 if rng.random() < 0.5 else (n + 1) // 2
    k = min(max(k, 1), n - 1)
    left, right = items[:k], items[k:]
    if target >= 1.0:
        return (H, _initial_tree(left, aspects, target * k / n, rng),
                _initial_tree(right, aspects, target * (n - k) / n, rng))
    return (V, _initial_tree(left, aspects, target * n / k, rng),
            _initial_tree(right, aspects, target * n / (n - k), rng))


def _leaves(tree) -> list[int]:
    if isinstance(tree, int):
        return [tree]
    return _leaves(tree[1]) + _leaves(tree[2])


def _internal_paths(tree, path=()):
    if isinstance(tree, int):
        return []
    return [path] + _internal_paths(tree[1], path + (1,)) + _internal_paths(tree[2], path + (2,))


def _get(tree, path):
    for p in path:
        tree = tree[p]
    return tree


def _set(tree, path, value):
    if not path:
        return value
    node = list(tree)
    node[path[0]] = _set(tree[path[0]], path[1:], value)
    return tuple(node)


def _mutate(tree, aspects, rng) -> Tree:
    if isinstance(tree, int):
        return tree
    move = rng.random()
    if move < 0.35:  # swap two videos
        leaves = _leaves(tree)
        i, j = rng.sample(range(len(leaves)), 2)
        swap = {leaves[i]: leaves[j], leaves[j]: leaves[i]}

        def remap(t):
            return swap.get(t, t) if isinstance(t, int) else (t[0], remap(t[1]), remap(t[2]))

        return remap(tree)
    path = rng.choice(_internal_paths(tree))
    node = _get(tree, path)
    if move < 0.6:  # flip split direction
        new = (V if node[0] == H else H, node[1], node[2])
    elif move < 0.7:  # swap children
        new = (node[0], node[2], node[1])
    elif move < 0.85 and not isinstance(node[1], int):  # rotate right
        lft = node[1]
        new = (lft[0], lft[1], (node[0], lft[2], node[2]))
    elif not isinstance(node[2], int):  # rotate left
        rgt = node[2]
        new = (rgt[0], (node[0], node[1], rgt[1]), rgt[2])
    else:  # rebuild subtree
        items = _leaves(node)
        rng.shuffle(items)
        new = _initial_tree(items, aspects, rng.uniform(0.4, 2.5), rng)
    return _set(tree, path, new)


def puzzle_layout(aspects, width, height, gap=0.0, max_crop=0.12, evenness=1.0,
                  crop_cost=0.5, seed=0, time_budget=1.2, restarts=4,
                  max_iters=200_000) -> LayoutResult:
    """Fill the screen with all videos, keeping their sizes similar.

    Runs ``restarts`` independent annealing searches sharing ``time_budget``
    seconds and keeps the best. Deterministic for a given seed and iteration
    count; with a time budget the result depends slightly on machine speed.
    """
    n = len(aspects)
    if n == 0:
        return LayoutResult([], 0.0, 1.0, 0.0)
    if n == 1:
        return _finish(grid_layout(aspects, width, height, gap).rects, aspects, width, height)

    ev = _Evaluator(aspects, width, height, gap, max_crop, evenness, crop_cost)
    target = width / height

    # One start interleaves portrait and landscape videos; the rest are random.
    order = sorted(range(n), key=lambda i: aspects[i])
    mixed = [order[(i // 2) if i % 2 == 0 else n - 1 - i // 2] for i in range(n)]

    best_tree, best_score = None, -math.inf
    per_run = time_budget / restarts
    for run in range(restarts):
        rng = random.Random(seed * 1000 + run)
        items = mixed[:] if run == 0 else rng.sample(range(n), n)
        tree = _initial_tree(items, aspects, target, rng)
        tree, score = _anneal(tree, ev, aspects, rng, per_run, max_iters // restarts)
        if score > best_score:
            best_tree, best_score = tree, score

    return _finish(ev.place(best_tree), aspects, width, height)


def _anneal(tree, ev, aspects, rng, budget, max_iters, t_start=0.2, t_end=0.002):
    current = best = tree
    current_score = best_score = ev.score(tree)[0]
    start = time.perf_counter()
    temperature = t_start
    for i in range(max_iters):
        if i % 32 == 0:
            # Cool from hot to cold over whichever budget runs out first.
            progress = max((time.perf_counter() - start) / budget, i / max_iters)
            if progress >= 1.0:
                break
            temperature = t_start * (t_end / t_start) ** progress
        cand = _mutate(current, aspects, rng)
        s = ev.score(cand)[0]
        if s > current_score or rng.random() < math.exp((s - current_score) / temperature):
            current, current_score = cand, s
            if s > best_score:
                best, best_score = cand, s
    return best, best_score


def grid_layout(aspects, width, height, gap=0.0) -> LayoutResult:
    """Equal cells; the column count is the one that shows the most video."""
    n = len(aspects)
    if n == 0:
        return LayoutResult([], 0.0, 1.0, 0.0)
    best = None
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        cw = (width - gap * (cols - 1)) / cols
        ch = (height - gap * (rows - 1)) / rows
        if cw <= 0 or ch <= 0:
            continue
        covered = sum(min(cw, ch * a) * min(cw, ch * a) / a for a in aspects)
        if best is None or covered > best[0]:
            best = (covered, cols, rows, cw, ch)
    _, cols, rows, cw, ch = best
    rects = []
    for i, a in enumerate(aspects):
        r, c = divmod(i, cols)
        # Centre the last, possibly partial, row.
        in_row = min(cols, n - r * cols)
        offset = (width - (in_row * cw + (in_row - 1) * gap)) / 2
        cx = offset + c * (cw + gap)
        cy = r * (ch + gap) + (height - (rows * ch + (rows - 1) * gap)) / 2
        w = min(cw, ch * a)
        h = w / a
        rects.append(Rect(cx + (cw - w) / 2, cy + (ch - h) / 2, w, h))
    return _finish(rects, aspects, width, height)


def _finish(rects, aspects, width, height) -> LayoutResult:
    areas = [r.area for r in rects]
    return LayoutResult(
        rects=rects,
        coverage=sum(areas) / (width * height),
        size_spread=max(areas) / min(areas),
        max_crop=max(_crop(r, a) for r, a in zip(rects, aspects)),
    )


def justified_rows(aspects, width, height, gap=0.0, max_crop=0.12, evenness=1.0,
                   crop_cost=0.5) -> LayoutResult:
    """Rows that each span the full width, in playlist order (Google Photos style).

    Row breaks are chosen by dynamic programming so every row's natural height
    is close to the target; the remaining mismatch is absorbed by cropping
    within ``max_crop``, or by centring the rows if that isn't enough.
    """
    n = len(aspects)
    if n == 0:
        return LayoutResult([], 0.0, 1.0, 0.0)
    ev = _Evaluator(aspects, width, height, gap, max_crop, evenness, crop_cost)
    best = None
    for k in range(1, n + 1):
        target = (height - gap * (k - 1)) / k
        if target <= 1:
            break
        rows = _partition_rows(aspects, width, gap, target, k)
        rects = _place_rows(rows, aspects, width, height, gap, max_crop)
        score = ev.score_rects(rects)
        if best is None or score > best[0]:
            best = (score, rects)
    return _finish(best[1], aspects, width, height)


def justified_columns(aspects, width, height, **kwargs) -> LayoutResult:
    """Columns that each span the full height, in playlist order."""
    flipped = justified_rows([1 / a for a in aspects], height, width, **kwargs)
    rects = [Rect(r.y, r.x, r.h, r.w) for r in flipped.rects]
    return _finish(rects, aspects, width, height)


def _natural_height(aspects, width, gap, i, j):
    return (width - gap * (j - i - 1)) / sum(aspects[i:j])


def _partition_rows(aspects, width, gap, target, k):
    """Split aspects[0:n] into k consecutive rows with heights close to target."""
    n = len(aspects)
    inf = float("inf")
    cost = [[inf] * (n + 1) for _ in range(k + 1)]
    back = [[0] * (n + 1) for _ in range(k + 1)]
    cost[0][0] = 0.0
    for rows in range(1, k + 1):
        for j in range(rows, n + 1):
            for i in range(rows - 1, j):
                if cost[rows - 1][i] == inf:
                    continue
                h = _natural_height(aspects, width, gap, i, j)
                c = cost[rows - 1][i] + math.log(h / target) ** 2
                if c < cost[rows][j]:
                    cost[rows][j], back[rows][j] = c, i
    bounds, j = [], n
    for rows in range(k, 0, -1):
        i = back[rows][j]
        bounds.append((i, j))
        j = i
    return bounds[::-1]


def _place_rows(rows, aspects, width, height, gap, max_crop):
    keep = 1.0 - max_crop
    natural, lo, hi = [], [], []
    for i, j in rows:
        inner = width - gap * (j - i - 1)
        s = sum(aspects[i:j])
        natural.append(inner / s)
        hi.append(inner / (s * keep))  # taller: crop the sides
        lo.append(inner * keep / s)  # shorter: crop top and bottom
    avail = height - gap * (len(rows) - 1)
    heights = _fit_heights(natural, lo, hi, avail)
    scale = min(1.0, avail / sum(heights))
    heights = [h * scale for h in heights]
    used_h = sum(heights) + gap * (len(rows) - 1)
    y = (height - used_h) / 2
    rects: list[Rect | None] = [None] * len(aspects)
    for (i, j), h in zip(rows, heights):
        # Shrinking rows (scale < 1) shrinks widths too, so centre each row.
        widths = [aspects[t] * h for t in range(i, j)]
        inner = (width - gap * (j - i - 1)) * scale
        widths = [w * inner / sum(widths) for w in widths]
        x = (width - (inner + gap * (j - i - 1))) / 2
        for t, w in zip(range(i, j), widths):
            rects[t] = Rect(x, y, w, h)
            x += w + gap
        y += h + gap
    return rects


def _fit_heights(natural, lo, hi, total):
    """Adjust row heights within [lo, hi] so they sum to ``total`` if possible."""
    heights = list(natural)
    for _ in range(50):
        diff = total - sum(heights)
        if abs(diff) < 1e-6:
            break
        free = [i for i, h in enumerate(heights)
                if (diff > 0 and h < hi[i] - 1e-9) or (diff < 0 and h > lo[i] + 1e-9)]
        if not free:
            break
        weight = sum(natural[i] for i in free)
        for i in free:
            heights[i] = min(max(heights[i] + diff * natural[i] / weight, lo[i]), hi[i])
    return heights


def fixed_grid(aspects, width, height, cols, rows, gap=0.0) -> LayoutResult:
    """GridPlayer-style grid with a chosen number of columns and rows."""
    n = len(aspects)
    if n == 0:
        return LayoutResult([], 0.0, 1.0, 0.0)
    cols = max(1, cols or math.ceil(math.sqrt(n)))
    rows = max(1, rows or math.ceil(n / cols))
    if cols * rows < n:
        rows = math.ceil(n / cols)
    cw = (width - gap * (cols - 1)) / cols
    ch = (height - gap * (rows - 1)) / rows
    rects = []
    for i, a in enumerate(aspects):
        r, c = divmod(i, cols)
        w = min(cw, ch * a)
        h = w / a
        cx, cy = c * (cw + gap), r * (ch + gap)
        rects.append(Rect(cx + (cw - w) / 2, cy + (ch - h) / 2, w, h))
    return _finish(rects, aspects, width, height)


LAYOUT_MODES = {
    "puzzle": "Puzzle — fill the screen, similar sizes (reorders videos)",
    "rows": "Justified rows — keeps playlist order",
    "columns": "Justified columns — keeps playlist order",
    "grid": "Auto grid — equal cells, best column count",
    "fixed-grid": "Fixed grid — your rows × columns",
}


# --- Equal-area puzzle ------------------------------------------------------
#
# Every split divides its rectangle in proportion to the number of videos on
# each side (optionally nudged by up to ``slack``), so tiles get the same area
# by construction. The search decides the split direction, the split point and
# which videos go to each side so that every leaf's shape matches its video
# within the crop allowance. Videos are grouped into a few shape classes, so
# the search can memoise on (rectangle shape, videos per class) and stays fast.

_SHAPE_TOLERANCE = 0.06  # videos whose log-aspects differ less share a class
_ASPECT_BINS = 32  # memo resolution: bins per unit of log-aspect
MAX_SHAPE_CLASSES = 4
_BIG_GROUP = 8  # above this many videos, only near-even, fair-share splits are tried


def _shape_classes(aspects):
    order = sorted(range(len(aspects)), key=lambda i: aspects[i])
    classes: list[list[int]] = []
    for i in order:
        if classes and math.log(aspects[i]) - math.log(aspects[classes[-1][0]]) < _SHAPE_TOLERANCE:
            classes[-1].append(i)
        else:
            classes.append([i])
    # The search grows quickly with the number of classes: merge the two
    # closest neighbours until at most MAX_SHAPE_CLASSES remain. Rare odd shapes
    # then just get a little more cropping.
    def rep(c):
        return sum(math.log(aspects[i]) for i in c) / len(c)
    while len(classes) > MAX_SHAPE_CLASSES:
        gaps = [(rep(classes[j + 1]) - rep(classes[j])) * min(len(classes[j]), len(classes[j + 1]))
                for j in range(len(classes) - 1)]
        j = gaps.index(min(gaps))
        classes[j:j + 2] = [classes[j] + classes[j + 1]]
    reps = [math.exp(rep(c)) for c in classes]
    return classes, reps


class _EqualAreaSearch:
    def __init__(self, reps, max_crop, slack, crop_cost, time_budget=math.inf,
                 big_group=_BIG_GROUP, memo=None):
        self.reps = reps
        self.big_group = big_group
        self.deadline = time.perf_counter() + time_budget
        self.calls = 0
        self.crop_limit = -math.log(1.0 - max_crop) if max_crop > 0 else 0.0
        self.crop_cost = crop_cost
        self.slack = slack
        # Entries are exact results for (shape bin, counts) and don't depend on
        # the screen, so the memo is shared across calls (see _memo_for).
        self.memo: dict = {} if memo is None else memo

    def leaf_cost(self, aspect, cls):
        d = abs(math.log(aspect / self.reps[cls]))
        if d <= self.crop_limit:
            return self.crop_cost * (1.0 - math.exp(-d))
        # Beyond the crop limit the tile is letterboxed: lose that share of area.
        over = d - self.crop_limit
        return self.crop_cost * (1.0 - math.exp(-self.crop_limit)) + 1.5 * (1.0 - math.exp(-over))

    def best(self, aspect, counts):
        """Lowest cost (per tile, summed) to fill a rect of this aspect with ``counts``."""
        key = (round(math.log(aspect) * _ASPECT_BINS), counts)
        hit = self.memo.get(key)
        if hit is not None:
            return hit
        self.calls += 1
        if self.calls % 4096 == 0 and time.perf_counter() > self.deadline:
            raise TimeoutError
        k = sum(counts)
        if k == 1:
            cls = counts.index(1)
            result = (self.leaf_cost(aspect, cls), ("leaf", cls))
            self.memo[key] = result
            return result
        best = (math.inf, None)
        good_enough = 0.004 * k  # near-perfect fill: stop looking
        # Split along the long side first; it is almost always the better cut.
        ops = ("H", "V") if aspect >= 1.0 else ("V", "H")
        big = k > self.big_group
        for k1 in _split_sizes(k, big):
            exhaustive = not big and (min(k1, k - k1) <= 3 or k <= 8)
            for c1 in _sub_counts(counts, k1, exhaustive=exhaustive, window=0 if big else 2):
                c2 = tuple(a - b for a, b in zip(counts, c1))
                for f in self._fractions():
                    share = k1 / k * f
                    if not 0.02 < share < 0.98:
                        continue
                    penalty = self._imbalance(f, k1, k - k1)
                    for op in ops:
                        if op == "H":
                            a1, a2 = aspect * share, aspect * (1 - share)
                        else:
                            a1, a2 = aspect / share, aspect / (1 - share)
                        first = penalty + self.best(a1, c1)[0]
                        if first >= best[0]:
                            continue  # can't beat what we have
                        cost = first + self.best(a2, c2)[0]
                        if cost < best[0]:
                            best = (cost, (op, share, c1, c2))
                    if best[0] <= good_enough:
                        self.memo[key] = best
                        return best
        self.memo[key] = best
        return best

    def _fractions(self):
        if self.slack <= 0:
            return (1.0,)
        s = self.slack
        return (1.0, 1 - s, 1 + s)

    @staticmethod
    def _imbalance(f, k1, k2):
        # Tiles on each side grow/shrink by f and (1 - f*k1/k)/(k2/k); cost the
        # size change so exact equality wins unless it buys real coverage.
        return 0.0 if f == 1.0 else 0.15 * abs(math.log(f)) * (k1 + k2) / 4

    def build(self, aspect, counts):
        """Reconstruct the chosen tree as nested (op, share, left, right) / ("leaf", cls)."""
        _, choice = self.best(aspect, counts)
        if choice[0] == "leaf":
            return choice
        op, share, c1, c2 = choice
        if op == "H":
            return (op, share, self.build(aspect * share, c1), self.build(aspect * (1 - share), c2))
        return (op, share, self.build(aspect / share, c1), self.build(aspect / (1 - share), c2))


def _split_sizes(k, big=False):
    """Candidate sizes for the first group, most promising (near-halves) first.

    Big groups only split near the middle (or in thirds); the lopsided splits
    that fit an odd video beside a stack matter lower down the tree.
    """
    if big:
        order = [k // 2, (k + 1) // 2, k // 3, (2 * k) // 3, (k + 1) // 3, (2 * k + 2) // 3]
    else:
        order = [k // 2, (k + 1) // 2, k // 3, (2 * k) // 3, 1, k - 1, 2, k - 2, 3, k - 3]
    seen, sizes = set(), []
    for s in order:
        if 1 <= s <= k - 1 and s not in seen:
            seen.add(s)
            sizes.append(s)
    return sizes


def _sub_counts(counts, total, exhaustive=True, window=2):
    """Ways to take ``total`` items from the classes in ``counts``.

    For big groups only near-proportional takes are tried (each class within
    ``window`` of its fair share); good equal-area splits are always near that.
    The fair share comes first so good solutions are found early.
    """
    k = sum(counts)
    ranges = []
    for c in counts:
        fair = c * total / k
        if exhaustive:
            lo, hi = 0, c
        else:
            lo, hi = max(0, math.floor(fair) - window), min(c, math.ceil(fair) + window)
        ranges.append(sorted(range(lo, hi + 1), key=lambda t, fair=fair: abs(t - fair)))

    def rec(i, remaining):
        if i == len(counts) - 1:
            if remaining in ranges[i]:
                yield (remaining,)
            return
        for take in ranges[i]:
            if take > remaining:
                continue
            for rest in rec(i + 1, remaining - take):
                yield (take, *rest)
    yield from rec(0, total)


def equal_area_layout(aspects, width, height, gap=0.0, max_crop=0.12, balance=1.0,
                      crop_cost=0.5, time_budget=math.inf, big_group=_BIG_GROUP,
                      seed=0) -> LayoutResult | None:
    """Puzzle where every video gets (nearly) the same area.

    ``balance`` 1.0 forces exactly equal areas; lower values let each split
    deviate from equal by up to (1 - balance) to cover more of the screen.
    Returns None if the search runs past ``time_budget`` seconds.
    """
    n = len(aspects)
    if n == 0:
        return LayoutResult([], 0.0, 1.0, 0.0)
    if n == 1:
        return grid_layout(aspects, width, height, gap)
    classes, reps = _shape_classes(aspects)
    # Round class shapes slightly so near-identical sets share a cached memo.
    reps = [math.exp(round(math.log(r), 2)) for r in reps]
    slack = max(0.0, min(1.0 - balance, 0.6))
    memo = _memo_for((tuple(reps), round(max_crop, 3), slack, crop_cost, big_group))
    search = _EqualAreaSearch(reps, max_crop, slack, crop_cost, time_budget, big_group, memo)
    try:
        tree = search.build(width / height, tuple(len(c) for c in classes))
    except TimeoutError:
        return None
    pools = [list(c) for c in classes]
    if seed:  # reshuffle: same shapes, different videos in each slot
        rng = random.Random(seed)
        for pool in pools:
            rng.shuffle(pool)
    rects: list[Rect | None] = [None] * n
    keep = 1.0 - max_crop
    _place_equal(tree, 0.0, 0.0, float(width), float(height), gap, pools, aspects, keep, rects)
    return _finish(rects, aspects, width, height)


_MEMOS: dict = {}  # search memos by (shapes, crop, slack, cost, big_group), most recent last
_MEMO_LIMIT = 6


def _memo_for(key) -> dict:
    """Shared memo for a search setup. Adding a video or resizing the window
    then reuses nearly all earlier work, and a search that ran out of time
    resumes where it stopped on the next call."""
    memo = _MEMOS.pop(key, None)
    if memo is None:
        memo = {}
    _MEMOS[key] = memo
    while len(_MEMOS) > _MEMO_LIMIT:
        _MEMOS.pop(next(iter(_MEMOS)))
    return memo


def _place_equal(node, x, y, w, h, gap, pools, aspects, keep, rects):
    if node[0] == "leaf":
        idx = pools[node[1]].pop(0)
        a = aspects[idx]
        shown = w / h
        # Letterbox if the slot is more off-shape than cropping allows.
        if shown > a / keep:
            nw = h * a / keep
            x, w = x + (w - nw) / 2, nw
        elif shown < a * keep:
            nh = w / (a * keep)
            y, h = y + (h - nh) / 2, nh
        rects[idx] = Rect(x, y, max(w, 1.0), max(h, 1.0))
        return
    op, share, left, right = node
    if op == "H":
        inner = max(w - gap, 1.0)
        w1 = inner * share
        _place_equal(left, x, y, w1, h, gap, pools, aspects, keep, rects)
        _place_equal(right, x + w1 + gap, y, inner - w1, h, gap, pools, aspects, keep, rects)
    else:
        inner = max(h - gap, 1.0)
        h1 = inner * share
        _place_equal(left, x, y, w, h1, gap, pools, aspects, keep, rects)
        _place_equal(right, x, y + h1 + gap, w, inner - h1, gap, pools, aspects, keep, rects)


# --- Puzzle entry point -------------------------------------------------------

BALANCE_MODES = {
    "equal": "Equal — every video gets the same area",
    "balanced": "Balanced — sizes may differ a little to fill more",
    "fill": "Fill first — cover the screen, sizes vary more",
}
# How much a 2x size difference is worth in screen coverage, per mode.
_SPREAD_WEIGHT = {"equal": 0.3, "balanced": 0.12, "fill": 0.03}


def puzzle(aspects, width, height, gap=0.0, max_crop=0.12, balance="equal", seed=0,
           time_budget=1.2, quick=False) -> LayoutResult:
    """Best puzzle layout for the chosen size balance within ``time_budget``.

    Equal and balanced modes use the equal-area search (exact sizes, or sizes
    within ±15% per split for balanced); fill mode, and any search that runs
    out of time, uses the annealing search. The candidate with the best
    coverage/size-spread trade-off for the mode wins.
    """
    n = len(aspects)
    if n <= 1:
        return grid_layout(aspects, width, height, gap)
    deadline = time.perf_counter() + time_budget
    weight = _SPREAD_WEIGHT.get(balance, 0.3)

    def score(r):
        return r.coverage - weight * math.log2(max(r.size_spread, 1.0))

    candidates = []
    if balance in ("equal", "balanced"):
        plans = [(_BIG_GROUP, 0.0)]
        if balance == "balanced":
            plans.append((_BIG_GROUP, 0.15))
        if not quick:
            plans.append((12, 0.0))  # deeper exact search, if time allows
        for big, slack in plans:
            left = deadline - time.perf_counter()
            if left <= 0:
                break
            r = equal_area_layout(aspects, width, height, gap, max_crop, 1.0 - slack,
                                  time_budget=left, big_group=big, seed=seed)
            if r is not None:
                candidates.append(r)
    if not candidates or balance == "fill":
        left = max(deadline - time.perf_counter(), 0.08)
        evenness = {"fill": 0.6, "balanced": 1.0}.get(balance, 1.5)
        candidates.append(puzzle_layout(aspects, width, height, gap=gap, max_crop=max_crop,
                                        evenness=evenness, seed=seed, time_budget=left,
                                        restarts=1 if quick else 4))
    return max(candidates, key=score)
