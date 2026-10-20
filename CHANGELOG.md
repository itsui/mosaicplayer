# Changelog

## 0.3.0 — first public release

- Puzzle layouts that fill the screen with mixed portrait and landscape videos:
  equal sizes, balanced, or fill first; justified rows/columns; auto and
  fixed grids.
- One OpenGL canvas with parallel render threads and GPU decoding only where
  it pays off: smooth with dozens of videos, including 4K and 60 fps.
- Make single videos bigger or smaller, drag to swap, focus one, sync,
  random positions, zoom.
- Reads and writes GridPlayer `.gpls` playlists, which also keep MosaicPlayer's
  layout settings; VLC `.m3u` playlists work too.
- Windows installer and portable zip, Linux AppImage.
