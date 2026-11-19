"""Paths and font lookup shared by the README asset scripts."""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MEDIA = REPO / "media"
sys.path.insert(0, str(REPO))

_FONT_DIRS = [
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
    Path.home() / "Library/Fonts",
    Path("/Library/Fonts"),
    Path.home() / ".local/share/fonts",
    Path("/usr/share/fonts"),
]


def find_font(*names):
    """First installed font file among `names`, searched in the usual font directories."""
    for name in names:
        for d in _FONT_DIRS:
            hits = list(d.rglob(name)) if d.is_dir() else []
            if hits:
                return str(hits[0])
    sys.exit(f"None of these fonts are installed: {', '.join(names)}")
