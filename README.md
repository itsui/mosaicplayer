# GridMosaic

Play many videos at once, packed like a puzzle to fill the screen. A rebuild of
[GridPlayer](https://github.com/vzhd1701/gridplayer) that fixes its two main
problems: stuttering with many videos, and hand-picking rows and columns for
a mix of portrait and landscape videos. Reads and writes GridPlayer `.gpls`
playlists.

```bash
./gridmosaic.sh --setup                 # once: creates ~/.venvs/gridmosaic
./gridmosaic.sh ~/test.gpls             # or videos, folders (recursive), .m3u
./gridmosaic.sh --install-desktop-entry # optional: add to the applications menu
```

Needs libmpv (`libmpv2` on Debian/Ubuntu/Mint) and Python 3.11+.

## What's different from GridPlayer

| | GridPlayer 0.5.5 | GridMosaic |
|---|---|---|
| Player engine | VLC 3 (python-vlc), one process per 4 videos | libmpv, one process |
| GPU decoding | VDPAU (legacy API) | NVDEC, only where it pays off |
| Drawing | VLC draws into one X11 sub-window per video | every video renders into a texture; one OpenGL canvas composites them |
| Layout | equal grid, rows/columns set by hand | puzzle packing, justified rows/columns, auto or fixed grid |
| Loop | seek back 500 ms before the end | mpv's seamless `loop-file` |
| Missing files | dropped silently (only in the log) | skipped with a message; broken files show an error tile |

### Measured on this machine (RTX 3090, 24 threads, 2× 4K screens)

Your 32-video `test.gpls` (h264, 480p–1080p):

| | GridPlayer `vlc_hw` | GridPlayer `vlc_sw` | GridMosaic (auto) |
|---|---|---|---|
| Smooth? | no — NVDEC pinned at 100% | yes | yes — 60 redraws/s, ~29 fps per tile |
| CPU | ~1 core | ~6.5 cores | ~2.8 cores |
| RAM | 3.4 GB | ~3 GB | 1.2 GB |
| VRAM | — | — | 0 MB |
| Screen covered | 63% (your 4-row setting) | 63% | 97% |

Stress test, 60× 720p + 4× 4K: 60 redraws/s, 29.6 of 30 fps per tile.

**Why GridPlayer stutters:** each VLC player decodes through VDPAU. With the
same 32 clips looping in mpv, VDPAU kept NVDEC 97% busy against 46% for
native NVDEC. Many of the clips are 5 s long, so GridPlayer's seek-to-loop
also restarts ~5 decoders a second.

**Why not GPU zero-copy by default:** NVDEC zero-copy (CUDA→OpenGL interop) is
fastest but showed intermittent green line artifacts with many players
sharing one GL context. Copy mode is clean, but every GPU-decoded video costs
a CUDA context (a few hundred MB of VRAM). For small clips the CPU is clean,
cheap and uses no VRAM, so **auto** mode decodes small videos on the CPU and
only sends large ones (above ~1080p30, configurable) to NVDEC.

## Packing modes (keys 1–5)

1. **Puzzle** — a slicing tree (nested side-by-side/stacked splits) searched by
   simulated annealing: fills the screen, keeps tile sizes similar, reorders
   videos. With up to 12% crop it reaches 97–100% coverage at a 1.3–3×
   size spread; GridPlayer's best grid covers 63–79%.
2. **Justified rows** — Google-Photos style, keeps playlist order.
3. **Justified columns** — the same, rotated.
4. **Auto grid** — equal cells; picks the column count that shows the most video.
5. **Fixed grid** — your rows × columns, like GridPlayer.

The puzzle shows a quick layout at once and a refined one ~1 s later
(Settings → Layout → search time). `R` reshuffles it, `C` toggles cropping.
MaxRects-style bin packing doesn't fit this problem: tiles here are scalable,
not fixed-size boxes.

## Controls

| | |
|---|---|
| Click / double-click / Enter | pause one / focus one |
| Middle-click | mute or unmute one |
| Wheel, ← → | seek ±5 s (Shift+wheel ±30 s) |
| Ctrl+wheel, Z | zoom into the hovered video towards the cursor, reset zoom |
| Drag a tile onto another | swap them |
| Space, M | pause all, mute all |
| S, Y, Home | random positions, sync all to hovered, restart all |
| N, Del | random video from the same folder, remove |
| 1–5, R, C, [ ] | layout mode, reshuffle, crop, gap |
| F/F11, T, Ctrl+M, H | fullscreen, stay on top, menu bar, stats |
| Ctrl+O, Ctrl+Shift+O, Ctrl+U, Ctrl+L | open videos, add folder, URL, playlist |
| Ctrl+S, Ctrl+Shift+S, Ctrl+W | save playlist, save as, close all |
| Ctrl+, , F1 | settings, help |

Right-click anywhere for the full menu (also in fullscreen, where the menu bar
is hidden).

## Settings (Ctrl+,)

- **Layout:** packing mode, gap, max crop, fixed grid size, puzzle evenness and
  search time.
- **Playback:** what happens at the end of a video (loop, next in folder,
  random from folder, stop), start muted, start at random position, pause
  when minimised.
- **Decoding:** auto / GPU copy / GPU zero-copy / CPU, GPU API (NVDEC, VA-API,
  Vulkan), GPU decode budget, max GPU videos, auto threshold, scaling quality,
  render threads.
- **Interface:** titles (hover/always/never), hide mouse after, background
  colour, stay on top, menu bar, start fullscreen.
- **Playlists:** save positions, save paused state, recent playlists.

Stored in `~/.config/gridmosaic/gridmosaic.conf`.

## Layout of the code

```
gridmosaic/
  layout.py           packing algorithms (pure Python, unit-tested)
  playlist.py         .gpls / .m3u read+write, folder scanning
  tile.py             one mpv player per video, decoder policy
  render.py           render worker threads (shared GL contexts, double buffers)
  canvas.py           the OpenGL video wall: layout, compositing, mouse
  window.py           menus, shortcuts, drag and drop, open/save
  settings_dialog.py  the settings window
  config.py           persisted settings
tests/                layout, playlist and end-to-end tests (pytest)
```

Run the tests with `~/.venvs/gridmosaic/bin/python -m pytest tests/`. The
end-to-end test opens a small window with generated clips.
