"""Claude Code hook entry point: `hook.py snapshot|review`."""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from comment_gate.gate import main  # noqa: E402
except Exception as import_error:  # noqa: BLE001
    # `except ... as` unbinds its name when the block ends, so the fallback
    # keeps the error in a name of its own.
    e = import_error
    trace = traceback.format_exc(limit=6)

    # Fail open on a broken install and log the error because Claude Code
    # hides non-zero exits.
    def main(argv: Sequence[str] | None = None) -> int:
        argv = sys.argv if argv is None else argv
        event = argv[1] if len(argv) > 1 else ""
        root = Path(os.environ.get("COMMENT_GATE_HOME") or Path.home() / ".claude" / "comment-gate")
        try:
            root.mkdir(parents=True, exist_ok=True)
            rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": "error", "hook_event": event,
                   "error": f"import failed: {e!r}", "trace": trace}
            with open(root / "log.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass
        if event == "review":
            sys.stdout.write(json.dumps({"systemMessage": f"Comment gate error (ignored): import failed: {e}"}))
        return 0

if __name__ == "__main__":
    sys.exit(main())
