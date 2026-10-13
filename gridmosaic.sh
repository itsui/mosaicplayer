#!/usr/bin/env bash
# Launch GridMosaic from this checkout. First run: ./gridmosaic.sh --setup
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${GRIDMOSAIC_VENV:-$HOME/.venvs/gridmosaic}"

if [[ "${1:-}" == "--setup" ]]; then
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q -r "$HERE/requirements.txt"
    echo "Installed into $VENV"
    exit 0
fi

if [[ "${1:-}" == "--install-desktop-entry" ]]; then
    mkdir -p "$HOME/.local/share/applications"
    cat > "$HOME/.local/share/applications/gridmosaic.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=GridMosaic
Comment=Play many videos at once, packed to fill the screen
Exec="$HERE/gridmosaic.sh" %F
Icon=video-display
Terminal=false
Categories=AudioVideo;Video;Player;
MimeType=video/mp4;video/x-matroska;video/webm;video/quicktime;video/x-msvideo;
DESKTOP
    echo "Added GridMosaic to your applications menu"
    exit 0
fi

export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python" -m gridmosaic "$@"
