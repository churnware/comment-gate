"""Per-session state: pre-edit baselines of files Claude touched, the review
cache, and the event log. Everything lives under `config.state_root()`."""

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

from .config import state_root

MAX_FILE_BYTES = 1_000_000
STALE_SESSION_SEC = 7 * 24 * 3600


def _session_dir(session_id):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")
    return state_root() / "state" / safe


def _key(path):
    return hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode("utf-8")).hexdigest()


def read_text(path):
    """Return file text, or None if missing; raise ValueError if unsuitable."""
    p = Path(path)
    if not p.exists():
        return None
    if p.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("file too large")
    with open(p, encoding="utf-8", newline="") as f:
        return f.read()


def snapshot(session_id, path):
    """Record `path`'s content before Claude's first edit in this session."""
    d = _session_dir(session_id) / "baselines"
    d.mkdir(parents=True, exist_ok=True)
    target = d / (_key(path) + ".json")
    if target.exists():
        return False
    try:
        content = read_text(path)
    except (ValueError, UnicodeDecodeError, OSError):
        return False
    # Mode "x" makes concurrent snapshots of the same file race-safe: only the
    # first writer wins, so the earliest content is kept as the baseline.
    try:
        with open(target, "x", encoding="utf-8") as f:
            json.dump({"path": os.path.abspath(path), "content": content}, f)
    except FileExistsError:
        return False
    return True


def baselines(session_id):
    d = _session_dir(session_id) / "baselines"
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def clear_baselines(session_id):
    shutil.rmtree(_session_dir(session_id) / "baselines", ignore_errors=True)


def seen(session_id, digest):
    f = _session_dir(session_id) / "reviewed.txt"
    return f.is_file() and digest in f.read_text(encoding="utf-8").split()


def mark_seen(session_id, digest):
    d = _session_dir(session_id)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "reviewed.txt", "a", encoding="utf-8") as f:
        f.write(digest + "\n")


def flag(session_id, name, value=None):
    """Get (value=None) or set/clear a boolean marker file for the session."""
    f = _session_dir(session_id) / name
    if value is None:
        return f.exists()
    if value:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.touch()
    else:
        f.unlink(missing_ok=True)
    return value


def log(event, **fields):
    try:
        root = state_root()
        root.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **fields}
        with open(root / "log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def prune_stale():
    d = state_root() / "state"
    if not d.is_dir():
        return
    cutoff = time.time() - STALE_SESSION_SEC
    for s in d.iterdir():
        try:
            if s.stat().st_mtime < cutoff:
                shutil.rmtree(s, ignore_errors=True)
        except OSError:
            pass
