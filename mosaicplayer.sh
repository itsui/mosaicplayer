#!/usr/bin/env bash
# Launch MosaicPlayer from this checkout.
#   ./mosaicplayer.sh --setup              once: create the Python environment
#   ./mosaicplayer.sh --install            add MosaicPlayer to the applications menu
#   ./mosaicplayer.sh --uninstall          remove it from the menu again
#   ./mosaicplayer.sh [videos | folders | playlists]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${MOSAICPLAYER_VENV:-$HOME/.venvs/mosaicplayer}"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DESKTOP_FILE="$DATA/applications/mosaicplayer.desktop"
ICON_SIZES="16 24 32 48 64 128 256 512"

refresh_menus() {
    update-desktop-database -q "$DATA/applications" 2>/dev/null || true
    gtk-update-icon-cache -q -t "$DATA/icons/hicolor" 2>/dev/null || true
}

case "${1:-}" in
--setup)
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q -r "$HERE/requirements.txt"
    echo "Installed into $VENV"
    exit 0
    ;;
--install | --install-desktop-entry)
    mkdir -p "$(dirname "$DESKTOP_FILE")"
    for size in $ICON_SIZES; do
        mkdir -p "$DATA/icons/hicolor/${size}x${size}/apps"
        cp "$HERE/mosaicplayer/icons/mosaicplayer-$size.png" \
            "$DATA/icons/hicolor/${size}x${size}/apps/mosaicplayer.png"
    done
    cat > "$DESKTOP_FILE" <<DESKTOP
[Desktop Entry]
Type=Application
Name=MosaicPlayer
GenericName=Multi-video player
Comment=Play many videos at once, packed like a puzzle to fill the screen
Exec="$HERE/mosaicplayer.sh" %F
Icon=mosaicplayer
Terminal=false
StartupWMClass=MosaicPlayer
Categories=AudioVideo;Video;Player;
Keywords=video;grid;mosaic;wall;multi;gridplayer;
MimeType=video/mp4;video/x-matroska;video/webm;video/quicktime;video/x-msvideo;video/x-flv;video/mpeg;video/mp2t;
DESKTOP
    refresh_menus
    echo "Added MosaicPlayer to your applications menu (Sound & Video)."
    exit 0
    ;;
--uninstall)
    rm -f "$DESKTOP_FILE"
    for size in $ICON_SIZES; do
        rm -f "$DATA/icons/hicolor/${size}x${size}/apps/mosaicplayer.png"
    done
    refresh_menus
    echo "Removed MosaicPlayer from your applications menu (settings are kept)."
    exit 0
    ;;
esac

if [[ ! -x "$VENV/bin/python" ]]; then
    echo "MosaicPlayer isn't set up yet (no Python environment at $VENV)." >&2
    echo "Run: $HERE/mosaicplayer.sh --setup" >&2
    exit 1
fi

export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python" -m mosaicplayer "$@"
