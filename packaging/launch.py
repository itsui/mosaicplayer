"""Entry point for packaged builds (PyInstaller)."""

import sys

from mosaicplayer.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
