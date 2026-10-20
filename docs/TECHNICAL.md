# MosaicPlayer: technical notes

For developers and the curious. The [README](../README.md) is the user guide.

## Running from source

From source (any distro, needs Python 3.11+ and libmpv: `libmpv2` on
Debian/Ubuntu/Mint, `mpv-libs` on Fedora):

```bash
git clone https://github.com/itsui/mosaicplayer && cd mosaicplayer
./mosaicplayer.sh --setup       # once: creates ~/.venvs/mosaicplayer
./mosaicplayer.sh --install     # adds MosaicPlayer (with its icon) to the applications menu
./mosaicplayer.sh wall.gpls     # or videos, folders, .m3u
```

The menu entry runs the checkout, so changes to the code show up the next
time you start it. `--uninstall` removes the entry; your settings stay. Or
`pip install .` for a `mosaicplayer` command.

Command-line options apply to that run only (your saved settings stay as they
were, unless you change the same setting in the app):
`--layout {puzzle,rows,columns,grid,fixed-grid}`, `--decoder
{auto,gpu-copy,gpu-zero-copy,cpu}`, `--gap PX`, `--crop PERCENT`, `--no-crop`,
`--fullscreen`, `--debug`. Ctrl+C in the terminal closes it cleanly.

## What's different from GridPlayer

| | GridPlayer 0.5.5 | MosaicPlayer |
|---|---|---|
| Player engine | VLC 3 (python-vlc), one process per 4 videos | libmpv, one process |
| GPU decoding | VDPAU (legacy API) | NVDEC, only where it pays off |
| Drawing | VLC draws into one X11 sub-window per video | every video renders into a texture; one OpenGL canvas composites them |
| Layout | equal grid, rows/columns set by hand | puzzle packing, justified rows/columns, auto or fixed grid |
| Loop | seek back 500 ms before the end | mpv's seamless `loop-file` |
| Missing files | dropped silently (only in the log) | skipped with a message; broken files show an error tile |

### Measured (RTX 3090, 24 threads, 2× 4K screens)

A 32-video playlist (h264, 480p–1080p, portrait and landscape):

| | GridPlayer `vlc_hw` | GridPlayer `vlc_sw` | MosaicPlayer (auto) |
|---|---|---|---|
| Smooth? | no — NVDEC pinned at 100% | yes | yes — 60 redraws/s, ~29 fps per tile |
| CPU | ~1 core | ~6.5 cores | ~2.8 cores |
| RAM | 3.4 GB | ~3 GB | 1.2 GB |
| VRAM | — | — | 0 MB |
| Screen covered | 63% (4 rows set by hand) | 63% | 97% |

Stress test, 60× 720p + 4× 4K: 60 redraws/s, 29.6 of 30 fps per tile.

**Why GridPlayer stutters:** each VLC player decodes through VDPAU. With the
same 32 clips looping in mpv, VDPAU kept NVDEC 97% busy against 46% for
native NVDEC. Many of the clips are 5 s long, so GridPlayer's seek-to-loop
also restarts ~5 decoders a second.

**Why not GPU decoding for everything:** every GPU-decoded video costs a CUDA
context (a few hundred MB of VRAM); in zero-copy mode every tile does, because
mpv sets up the CUDA→OpenGL interop per player. On the 32-video playlist,
zero-copy used 9.6 GB of VRAM and 3.9 GB of RAM against 0 GB and 1.2 GB for
auto. For small clips the CPU is cheap and uses no VRAM, so **auto** decodes
small videos on the CPU and sends only large ones (above ~1080p30,
configurable) to NVDEC in copy mode.

Frames pass between the render threads and the window through GPU fences:
the window waits (on the GPU) until a frame is fully rendered before drawing
it, and a render thread waits until the window has finished drawing from a
buffer before reusing it. Without the second fence, tiles flickered black
under heavy load (2% of frames with 17 videos at 1216×2160, 60 fps); without
the first, zero-copy decoding showed green line artifacts.

## Packing modes in detail (keys 1–5)

1. **Puzzle** — fills the screen with all videos, reordering them freely.
   *Tile sizes* (Layout menu, Settings, or `B`):
   - **Equal** (default): every video gets the same area. A memoised search
     over slicing trees whose splits follow the video count on each side, so
     equal areas are built in; it chooses split directions and which shapes
     go where. Then each split is nudged so the *visible* sizes match (a
     letterboxed video shows less than its slot).
   - **Balanced**: the same layout, with the splits nudged further to fill
     more of the screen while keeping sizes fairly close.
   - **Fill first**: simulated annealing that maximises coverage, letting
     sizes vary 1.5–3×.

   On the 32-video playlist (size spread / screen covered):

   | | Equal | Balanced | Fill first |
   |---|---|---|---|
   | No crop | 1.23× / 91.8% | 1.48× / 96.7% | 2.51× / 98.8% |
   | 15% crop | 1.05× / 98.1% | 1.09× / 98.4% | 1.39× / 98.8% |

   With a little crop allowed, every mode fills the screen and the
   difference between them is small; without it, they trade evenly between
   equal sizes and no gaps.

   Make the hovered video bigger or smaller with **+ / −** (or Alt+wheel, or
   right-click → Size); the others re-pack around it. **0** resets it. Sizes
   are saved in the playlist (`gm_size_weight`, ignored by GridPlayer).
   Dragging a video onto a similarly shaped one swaps them, and the swap
   sticks. For completely free arrangement, use justified rows or a grid.
2. **Justified rows** — Google-Photos style, keeps playlist order.
3. **Justified columns** — the same, rotated.
4. **Auto grid** — equal cells; picks the column count that shows the most video.
5. **Fixed grid** — your rows × columns, like GridPlayer.

The layout only changes when videos are added or removed, the window is
resized, or a setting or video size changes. Finished layouts are cached, so
focusing one video and coming back restores the exact same arrangement. New
videos get a place at once (a quick puzzle, or an instant grid if the search
needs longer), then glide to the refined layout (Settings → Layout → search
time); size and setting changes keep the current layout until the new one is
ready. Large equal-area searches that run past the search time continue in
further background passes instead of settling for an uneven layout. The equal-area search keeps its work between calls, so resizing the
window or adding a video re-solves in well under a second. `R` reshuffles it, `C` toggles cropping.
MaxRects-style bin packing doesn't fit this problem: tiles here are scalable,
not fixed-size boxes.

## Playlists and their settings

Saving a playlist (Ctrl+S) also saves its layout: packing mode, tile sizes,
crop, gap, fixed grid size, what happens at the end of a video and the
reshuffle (`R`) arrangement, plus each video's size. Opening it on its own
brings all of that back; a playlist without them (say, from GridPlayer) opens
with your defaults, and so does Close all.

Changing these from the menus or keys affects only the current playlist.
**Settings → Save current settings as default** makes them the defaults for
new videos and other playlists; **Reset to default settings** goes back.
Changes made in the settings window become defaults straight away.
Command-line options win over a playlist's settings for that run.

The settings live in the playlist's `#P:` line under `"mosaicplayer"`.
GridPlayer opens these playlists normally, but drops that part if it saves
them again.

## Settings (Ctrl+,)

- **Layout:** packing mode, gap, max crop, fixed grid size, puzzle tile sizes
  and search time.
- **Playback:** what happens at the end of a video (loop, next in folder,
  random from folder, stop), start muted, start at random position, pause
  when minimised.
- **Decoding:** auto / GPU copy / GPU zero-copy / CPU, GPU API (NVDEC, VA-API,
  Vulkan), GPU decode budget, max GPU videos, auto threshold, scaling quality,
  render threads.
- **Interface:** titles (hover/always/never), hide mouse after, background
  colour, stay on top, menu bar, start fullscreen.
- **Playlists:** save positions, save paused state, recent playlists.

Stored in `~/.config/mosaicplayer/mosaicplayer.conf`.

Folders are scanned with their subfolders, in natural order ("2" before "10"),
skipping hidden folders such as `.Trash-1000`. `.m3u` playlists from VLC
(`file://` entries) work too.

Adding more than 64 videos at once asks first (add all, or 64 at random):
every video is its own player.

## Troubleshooting

- **Everything reacts about once a second while the screen is off or locked.**
  The NVIDIA driver throttles vsynced OpenGL windows that aren't displayed, which
  stalls the UI thread. It stops when the screen is back. For benchmarks with
  the screen off, run with `MOSAICPLAYER_NO_VSYNC=1`.
- **Black bars around some tiles in the puzzle.** Equal sizes can't always fit
  every shape exactly. Allow cropping (`C`; 15% unless set otherwise in
  Settings) or use *Fill first* (`B`).

## Layout of the code

```
mosaicplayer/
  layout.py           packing algorithms (pure Python, unit-tested)
  playlist.py         .gpls / .m3u read+write, folder scanning
  tile.py             one mpv player per video, decoder policy
  render.py           render worker threads (shared GL contexts, double buffers)
  canvas.py           the OpenGL video wall: layout, compositing, mouse
  window.py           menus, shortcuts, drag and drop, open/save
  icons/              app icon in the sizes the menu and taskbar use
  settings_dialog.py  the settings window
  config.py           persisted settings
  options.py          option names (importable without libmpv)
tests/                layout, playlist, config and end-to-end tests (pytest)
```

Run the tests with `~/.venvs/mosaicplayer/bin/python -m pytest tests/`. The
end-to-end tests open a small window with generated clips (they need a display
and ffmpeg) and keep their settings in a temporary folder.

## Building the downloads

Push a version tag (`git tag v0.3.0 && git push origin v0.3.0`): GitHub
Actions builds the Windows installer, the portable zip and the AppImage, and
attaches them to a draft release to check before publishing. Locally:
`packaging/linux/build-appimage.sh` on Linux; on Windows see
`.github/workflows/release.yml` (PyInstaller + Inno Setup, with libmpv from
[shinchiro's mpv builds](https://github.com/shinchiro/mpv-winbuild-cmake)).

