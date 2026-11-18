"""`comment-gate` command entry point (the PATH shims launch this file)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from comment_gate.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
