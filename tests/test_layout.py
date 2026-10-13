import itertools
import random

import pytest

from gridmosaic.layout import grid_layout, puzzle_layout

EPS = 1e-6
PORTRAIT, LANDSCAPE, OLD_TV = 9 / 16, 16 / 9, 4 / 3


def _mixed(n, seed=0):
    rng = random.Random(seed)
    return [rng.choice([PORTRAIT, LANDSCAPE, LANDSCAPE, OLD_TV, 3 / 4]) for _ in range(n)]


def _assert_valid(result, n, width, height):
    assert len(result.rects) == n
    for r in result.rects:
        assert r.w > 0 and r.h > 0
        assert r.x >= -EPS and r.y >= -EPS
        assert r.x + r.w <= width + 1e-3 and r.y + r.h <= height + 1e-3
    for a, b in itertools.combinations(result.rects, 2):
        overlap_w = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
        overlap_h = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
        assert overlap_w <= 1e-3 or overlap_h <= 1e-3, (a, b)


@pytest.mark.parametrize("n", [1, 2, 3, 7, 23, 40])
@pytest.mark.parametrize("screen", [(3840, 2160), (1080, 1920), (1280, 1024)])
def test_puzzle_layout_is_valid(n, screen):
    aspects = _mixed(n, seed=n)
    result = puzzle_layout(aspects, *screen, time_budget=0.1)
    _assert_valid(result, n, *screen)
    assert result.max_crop <= 0.12 + 1e-6


def test_puzzle_respects_no_crop():
    aspects = _mixed(20)
    result = puzzle_layout(aspects, 3840, 2160, max_crop=0.0, time_budget=0.1)
    _assert_valid(result, 20, 3840, 2160)
    assert result.max_crop < 1e-6


def test_puzzle_beats_grid_on_mixed_orientations():
    aspects = [LANDSCAPE] * 19 + [PORTRAIT] * 13  # same mix as a real test playlist
    grid = grid_layout(aspects, 3840, 2160)
    puzzle = puzzle_layout(aspects, 3840, 2160, time_budget=0.6)
    assert puzzle.coverage > 0.95
    assert puzzle.coverage > grid.coverage + 0.2
    assert puzzle.size_spread < 3.5


def test_gaps_are_kept():
    aspects = _mixed(12)
    result = puzzle_layout(aspects, 1920, 1080, gap=6, time_budget=0.1)
    _assert_valid(result, 12, 1920, 1080)
    for a, b in itertools.combinations(result.rects, 2):
        apart_x = max(b.x - (a.x + a.w), a.x - (b.x + b.w))
        apart_y = max(b.y - (a.y + a.h), a.y - (b.y + b.h))
        assert max(apart_x, apart_y) >= 6 - 1e-3


def test_grid_layout_picks_columns_and_keeps_aspect():
    aspects = [LANDSCAPE] * 6
    result = grid_layout(aspects, 3840, 2160)
    _assert_valid(result, 6, 3840, 2160)
    for r in result.rects:
        assert r.w / r.h == pytest.approx(LANDSCAPE)
    assert result.max_crop < 1e-6


def test_same_seed_same_layout():
    aspects = _mixed(15)
    a = puzzle_layout(aspects, 1920, 1080, time_budget=10, max_iters=800)
    b = puzzle_layout(aspects, 1920, 1080, time_budget=10, max_iters=800)
    assert a.rects == b.rects


def test_empty():
    assert puzzle_layout([], 100, 100).rects == []
    assert grid_layout([], 100, 100).rects == []
