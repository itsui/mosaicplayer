#!/usr/bin/env bash
# Build dist/MosaicPlayer-<version>-x86_64.AppImage.
# Needs: python3 with the requirements and pyinstaller installed, libmpv2,
# and appimagetool (downloaded here if missing). Build on an older distro
# (e.g. Ubuntu 22.04) so the AppImage also runs on older systems.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"
VERSION="$("$PYTHON" -c 'import mosaicplayer; print(mosaicplayer.__version__)')"

export LIBMPV_SO="${LIBMPV_SO:-$(ldconfig -p | awk '/libmpv\.so\.[0-9] / {print $NF; exit}')}"
[[ -f "$LIBMPV_SO" ]] || { echo "libmpv.so.2 not found: install libmpv2 or set LIBMPV_SO" >&2; exit 1; }

"$PYTHON" -m PyInstaller --noconfirm --clean packaging/mosaicplayer.spec

APPDIR=build/AppDir
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -a dist/MosaicPlayer/. "$APPDIR/usr/bin/"
cp mosaicplayer/icons/mosaicplayer-256.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/mosaicplayer.png"
cp mosaicplayer/icons/mosaicplayer-256.png "$APPDIR/mosaicplayer.png"
cp packaging/linux/mosaicplayer.desktop "$APPDIR/usr/share/applications/"
cp packaging/linux/mosaicplayer.desktop "$APPDIR/"
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/MosaicPlayer" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

TOOL="${APPIMAGETOOL:-build/appimagetool-x86_64.AppImage}"
if [[ ! -x "$TOOL" ]]; then
    curl -fsSL -o "$TOOL" \
        https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x "$TOOL"
fi
# --appimage-extract-and-run: works without FUSE (CI runners, containers)
ARCH=x86_64 "$TOOL" --appimage-extract-and-run "$APPDIR" "dist/MosaicPlayer-$VERSION-x86_64.AppImage"
echo "Built dist/MosaicPlayer-$VERSION-x86_64.AppImage"
