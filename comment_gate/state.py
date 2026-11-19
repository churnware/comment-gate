"""Per-session state: pre-edit baselines of files Claude touched, the review
cache, and the event log. Everything lives under `config.state_root()`."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from collections.abc import Iterable
from pathlib import Path
from typing import TypedDict

from .config import state_root


class _Baseline(TypedDict):
    path: str
    # None: the file did not exist before the edit.
    content: str | None
    # The subagent that made the edit; None for the main agent.
    agent_id: str | None


class Baseline(_Baseline, total=False):
    deferred: bool

MAX_FILE_BYTES = 1_000_000
STALE_SESSION_SEC = 7 * 24 * 3600


def _session_dir(session_id: str | None) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")
    return state_root() / "state" / safe


def _key(path: str) -> str:
    return hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode("utf-8")).hexdigest()


def read_text(path: str | Path) -> str | None:
    """Return file text, or None if missing; raise ValueError if unsuitable."""
    p = Path(path)
    if not p.exists():
        return None
    if p.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("file too large")
    with open(p, encoding="utf-8", newline="") as f:
        return f.read()


def snapshot(session_id: str | None, path: str, agent_id: str | None = None) -> bool:
    """Record `path`'s content before Claude's first edit in this session.
    `agent_id` names the subagent that made the edit (None: the main agent)."""
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
            record: Baseline = {"path": os.path.abspath(path), "content": content, "agent_id": agent_id}
            json.dump(record, f)
    except FileExistsError:
        return False
    return True


def baselines(session_id: str | None) -> list[Baseline]:
    d = _session_dir(session_id) / "baselines"
    if not d.is_dir():
        return []
    out: list[Baseline] = []
    for f in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def clear_baselines(session_id: str | None, paths: Iterable[str] | None = None) -> None:
    """Drop the baselines of `paths`, or of every file when `paths` is None."""
    d = _session_dir(session_id) / "baselines"
    if paths is None:
        shutil.rmtree(d, ignore_errors=True)
        return
    for p in paths:
        (d / (_key(p) + ".json")).unlink(missing_ok=True)


def defer(session_id: str | None, path: str) -> None:
    """Keep `path`'s baseline for the main agent's next review."""
    f = _session_dir(session_id) / "baselines" / (_key(path) + ".json")
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    data.update(agent_id=None, deferred=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, f)


def note_outside(session_id: str | None, path: str) -> None:
    """Remember an edited file that lies outside the project, for the next notice."""
    d = _session_dir(session_id)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "outside.txt", "a", encoding="utf-8") as f:
        f.write(os.path.abspath(path) + "\n")


def take_outside(session_id: str | None) -> list[str]:
    """Return and forget the outside-project files noted since the last call."""
    f = _session_dir(session_id) / "outside.txt"
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
        f.unlink()
    except OSError:
        return []
    return list(dict.fromkeys(l for l in lines if l))


def seen(session_id: str | None, digest: str) -> bool:
    f = _session_dir(session_id) / "reviewed.txt"
    return f.is_file() and digest in f.read_text(encoding="utf-8").split()


def mark_seen(session_id: str | None, digest: str) -> None:
    d = _session_dir(session_id)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "reviewed.txt", "a", encoding="utf-8") as f:
        f.write(digest + "\n")


def flag(session_id: str | None, name: str, value: bool | None = None) -> bool:
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


def log(event: str, **fields: object) -> None:
    try:
        root = state_root()
        root.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **fields}
        with open(root / "log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def prune_stale() -> None:
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
