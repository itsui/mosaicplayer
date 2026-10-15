import itertools
import random
import time

import pytest

from mosaicplayer.layout import (equal_area_layout, fixed_grid, grid_layout, justified_columns,
                               justified_rows, puzzle, puzzle_layout)

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
    # Bounded by iterations, not time, so a busy machine gives the same result.
    puzzle = puzzle_layout(aspects, 3840, 2160, time_budget=60, max_iters=24000)
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


@pytest.mark.parametrize("fn", [justified_rows, justified_columns])
@pytest.mark.parametrize("n", [1, 5, 23, 32])
def test_justified_layouts_valid_and_ordered(fn, n):
    aspects = _mixed(n, seed=n + 7)
    result = fn(aspects, 3840, 2160, gap=2, max_crop=0.12)
    _assert_valid(result, n, 3840, 2160)
    assert result.max_crop <= 0.12 + 1e-6
    if fn is justified_rows and n > 1:
        # playlist order reads left-to-right, top-to-bottom
        keys = [(round(r.y), r.x) for r in result.rects]
        assert keys == sorted(keys)


def test_justified_rows_fill_well():
    aspects = [LANDSCAPE] * 19 + [PORTRAIT] * 13
    result = justified_rows(aspects, 3840, 2160)
    assert result.coverage > 0.9


def test_fixed_grid_respects_shape():
    aspects = [LANDSCAPE] * 10
    result = fixed_grid(aspects, 1920, 1080, cols=5, rows=2)
    _assert_valid(result, 10, 1920, 1080)
    assert len({round(r.y) for r in result.rects}) == 2


@pytest.mark.parametrize("n", [2, 5, 13, 32])
@pytest.mark.parametrize("screen", [(3840, 2160), (1080, 1920)])
def test_equal_area_layout_valid(n, screen):
    aspects = _mixed(n, seed=n + 3)
    result = equal_area_layout(aspects, *screen, gap=2, max_crop=0.15)
    _assert_valid(result, n, *screen)
    assert result.max_crop <= 0.15 + 1e-6


def test_equal_area_gives_even_sizes():
    aspects = [LANDSCAPE] * 17 + [PORTRAIT] * 12 + [OLD_TV] * 2 + [3 / 4]
    equal = equal_area_layout(aspects, 3840, 2160, max_crop=0.15)
    assert equal.coverage > 0.93
    assert equal.size_spread < 1.5
    fill = puzzle_layout(aspects, 3840, 2160, time_budget=60, max_iters=6000)
    assert equal.size_spread < fill.size_spread


@pytest.mark.parametrize("balance", ["equal", "balanced", "fill"])
def test_puzzle_modes_valid(balance):
    aspects = _mixed(20, seed=11)
    result = puzzle(aspects, 1920, 1080, gap=2, max_crop=0.15, balance=balance, time_budget=0.6)
    _assert_valid(result, 20, 1920, 1080)


def test_equal_area_reuses_work_between_calls():
    aspects = _mixed(40, seed=5)
    equal_area_layout(aspects, 1920, 1080, max_crop=0.15)
    start = time.perf_counter()
    equal_area_layout(aspects, 1600, 1000, max_crop=0.15)  # resized window
    assert time.perf_counter() - start < 0.5


def test_equal_area_reshuffle_keeps_shapes():
    aspects = [LANDSCAPE] * 6 + [PORTRAIT] * 3
    a = equal_area_layout(aspects, 1920, 1080, seed=0)
    b = equal_area_layout(aspects, 1920, 1080, seed=7)
    assert sorted((round(r.w), round(r.h)) for r in a.rects) == \
        sorted((round(r.w), round(r.h)) for r in b.rects)
    assert a.rects != b.rects


def test_weights_scale_tile_areas():
    aspects = [LANDSCAPE] * 17 + [PORTRAIT] * 12 + [OLD_TV] * 2 + [3 / 4]
    weights = [1.0] * len(aspects)
    weights[5] = 4.0
    weights[20] = 0.5
    result = equal_area_layout(aspects, 2233, 1228, gap=3, max_crop=0.15, weights=weights)
    _assert_valid(result, len(aspects), 2233, 1228)
    areas = sorted(r.area for r in result.rects)
    median = areas[len(areas) // 2]
    assert 3.3 < result.rects[5].area / median < 4.5
    assert 0.4 < result.rects[20].area / median < 0.6


def test_slots_are_assigned_by_playlist_position():
    # Slots go to videos by playlist position within a shape class, so swapping
    # two same-shaped videos in the playlist swaps their places on screen.
    aspects = [LANDSCAPE, PORTRAIT, LANDSCAPE, LANDSCAPE, PORTRAIT, LANDSCAPE]
    first = equal_area_layout(aspects, 1920, 1080).rects
    again = equal_area_layout(list(aspects), 1600, 900)
    scale = 1600 / 1920
    for a, b in zip(first, again.rects):
        assert abs(a.x * scale - b.x) < 2 and abs(a.y * scale - b.y) < 2


@pytest.mark.parametrize("fn", [
    lambda a, w, h: grid_layout(a, w, h, gap=40),
    lambda a, w, h: fixed_grid(a, w, h, 4, 4, gap=40),
    lambda a, w, h: justified_rows(a, w, h, gap=40),
    lambda a, w, h: justified_columns(a, w, h, gap=40),
    lambda a, w, h: puzzle(a, w, h, gap=40, time_budget=0.1),
])
@pytest.mark.parametrize("size", [(60, 40), (0, 0), (1, 300)])
def test_tiny_canvas_never_crashes(fn, size):
    result = fn(_mixed(12), *size)
    assert len(result.rects) == 12
    assert all(r.w > 0 and r.h > 0 for r in result.rects)


def test_justified_rows_scale_to_many_videos():
    aspects = _mixed(120, seed=9)
    start = time.perf_counter()
    result = justified_rows(aspects, 3840, 2160, gap=2)
    assert time.perf_counter() - start < 1.0  # was O(n^4): 4 s for 100 videos
    assert result.coverage > 0.9


def test_puzzle_abort_returns_none_quickly():
    aspects = _mixed(70, seed=4)
    start = time.perf_counter()
    result = puzzle(aspects, 3840, 2160, time_budget=10,
                    abort=lambda: time.perf_counter() - start > 0.15)
    assert result is None
    assert time.perf_counter() - start < 1.5


@pytest.mark.parametrize("weights", [[10.0, 0.1], [6.0, 0.25], [6.0] + [0.25] * 7])
def test_extreme_weights_still_lay_out(weights):
    result = equal_area_layout([LANDSCAPE] * len(weights), 1920, 1080, weights=weights)
    _assert_valid(result, len(weights), 1920, 1080)


def test_quick_layout_stays_quick_and_large_searches_resume():
    aspects = _mixed(100, seed=21) + [1.0] * 10  # five shapes: a slow equal-area search
    import mosaicplayer.layout as layout_module
    layout_module._MEMOS.clear()
    start = time.perf_counter()
    quick = puzzle(aspects, 2233, 1228, max_crop=0.15, time_budget=0.12, quick=True)
    assert time.perf_counter() - start < 0.3  # runs on the UI thread
    _assert_valid(quick, len(aspects), 2233, 1228)
    # Background passes resume the memoised search until the equal-area layout exists.
    for _ in range(4):
        result = puzzle(aspects, 2233, 1228, max_crop=0.15, time_budget=2.5)
        if result.method == "equal":
            break
    assert result.method == "equal"
    assert result.size_spread < 2.0


def test_equal_area_avoids_one_tiny_letterboxed_video():
    # A capped letterbox cost made showing one video 5x smaller than the rest
    # look cheaper than several mild mismatches.
    aspects = [4 / 3, 7 / 3, 7 / 3, 4 / 3, 4 / 3, 16 / 9]
    r = equal_area_layout(aspects, 2233, 1228, gap=2, max_crop=0.15)
    assert r.size_spread < 2.0


def test_justified_layouts_stay_fast_with_many_videos():
    rng = random.Random(5)
    aspects = [rng.choice([16 / 9, 9 / 16, 4 / 3, 3 / 4, 1.0, 21 / 9, 0.47]) for _ in range(150)]
    for fn in (justified_rows, justified_columns):
        start = time.perf_counter()
        r = fn(aspects, 3840, 2100, gap=2, max_crop=0.0)
        assert time.perf_counter() - start < 0.4  # runs on the UI thread (was 0.3-0.5 s)
        assert len(r.rects) == 150 and r.coverage > 0.6



MIXED_32 = [0.46] + [9 / 16] * 11 + [0.75] + [1.34] * 2 + [16 / 9] * 17  # like a real playlist


def test_equal_mode_evens_out_what_is_visible():
    # Without cropping, equal slots letterbox some videos; polishing the
    # splits on the visible areas made this 1.29x instead of 1.46x.
    r = puzzle(MIXED_32, 2233, 1228, gap=2, max_crop=0.0, balance="equal", time_budget=5)
    assert r.size_spread < 1.35 and r.coverage > 0.9


def test_balanced_sits_between_equal_and_fill():
    # Balanced used to give exactly the equal layout for most playlists.
    results = {b: puzzle(MIXED_32, 2233, 1228, gap=2, max_crop=0.0, balance=b, time_budget=5)
               for b in ("equal", "balanced", "fill")}
    equal, balanced, fill = results["equal"], results["balanced"], results["fill"]
    assert equal.rects != balanced.rects
    assert equal.coverage < balanced.coverage <= fill.coverage + 0.01
    assert equal.size_spread < balanced.size_spread < fill.size_spread
