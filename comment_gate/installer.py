"""Add or remove the gate's hooks in a project's Claude Code settings, and
remember which projects have it installed."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, TypeAlias

from . import config
from .config import Mode

# A Claude Code settings file. Its shape is Claude Code's, so it stays loosely typed.
Settings: TypeAlias = dict[str, Any]

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hook.py"
OURS = re.compile(r"hook\.py\"?\s+(snapshot|review)\s*$")
SETTINGS: dict[bool, str] = {False: "settings.local.json", True: "settings.json"}


def _command(event: str) -> str:
    return f'"{Path(sys.executable).as_posix()}" "{HOOK.as_posix()}" {event}'


def settings_path(project: str | Path, shared: bool) -> Path:
    return Path(project) / ".claude" / SETTINGS[shared]


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _save(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _strip_ours(settings: Settings) -> Settings:
    hooks = settings.get("hooks", {})
    for event in list(hooks):
        groups: list[dict[str, Any]] = []
        for g in hooks[event]:
            kept = [h for h in g.get("hooks", []) if not OURS.search(h.get("command", ""))]
            if kept:
                groups.append({**g, "hooks": kept})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    if not hooks:
        settings.pop("hooks", None)
    return settings


def git_root(path: str | Path) -> Path | None:
    for p in [Path(path), *Path(path).parents]:
        if (p / ".git").exists():
            return p
    return None


def install(project: str | Path, shared: bool = False, mode: Mode | None = None) -> tuple[Path, Path, bool]:
    """Install hooks; returns (settings_path, config_path, config_created)."""
    project = Path(project)
    cfg_path = project / config.CONFIG_RELPATH
    created = not cfg_path.exists()
    # A new project file holds only what install was told to set, so every
    # other setting keeps following the global config.
    if created or mode:
        data: Settings = _load(cfg_path)
        if mode:
            data["mode"] = mode
        _save(cfg_path, data)
    cfg = config.load(project)

    path = settings_path(project, shared)
    settings = _strip_ours(_load(path))
    hooks = settings.setdefault("hooks", {})
    hooks.setdefault("PreToolUse", []).append({
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [{"type": "command", "command": _command("snapshot"), "timeout": 10}],
    })
    # SubagentStop reviews each subagent's edits as it finishes, so the main
    # agent's Stop never reviews files a background subagent is still editing.
    for event in ("Stop", "SubagentStop"):
        hooks.setdefault(event, []).append({
            "hooks": [{"type": "command", "command": _command("review"), "timeout": int(cfg["timeout_sec"]) + 30}],
        })
    _save(path, settings)
    remember(project, True)
    return path, cfg_path, created


def uninstall(project: str | Path) -> list[Path]:
    """Remove our hooks from both settings files; returns the files changed."""
    changed: list[Path] = []
    for shared in (False, True):
        path = settings_path(project, shared)
        if path.is_file():
            before = _load(path)
            after = _strip_ours(json.loads(json.dumps(before)))
            if after != before:
                _save(path, after)
                changed.append(path)
    remember(project, False)
    return changed


def _our_events(project: str | Path, shared: bool) -> set[str]:
    try:
        settings = _load(settings_path(project, shared))
    except (OSError, ValueError):
        return set()
    return {event for event, gs in settings.get("hooks", {}).items()
            if any(OURS.search(h.get("command", "")) for g in gs for h in g.get("hooks", []))}


def hook_timeout(project: str | Path, shared: bool) -> int | None:
    """Return this tool's Stop hook timeout from the selected settings file. Return None
    if the file cannot be loaded or the hook or timeout is absent.
    """
    try:
        settings = _load(settings_path(project, shared))
    except (OSError, ValueError):
        return None
    timeout: int | None = next((h.get("timeout") for g in settings.get("hooks", {}).get("Stop", [])
                 for h in g.get("hooks", []) if OURS.search(h.get("command", ""))), None)
    return timeout


def installed_in(project: str | Path) -> list[str]:
    return [settings_path(project, shared).name for shared in (False, True) if _our_events(project, shared)]


def has_subagent_hook(project: str | Path) -> bool:
    """False for installs made before SubagentStop was hooked."""
    return any("SubagentStop" in _our_events(project, shared) for shared in (False, True))


def _registry() -> Path:
    return config.state_root() / "projects.json"


def known_projects() -> list[Path]:
    try:
        paths = json.loads(_registry().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [Path(p) for p in paths if Path(p).is_dir()]


def remember(project: str | Path, present: bool) -> None:
    key = str(Path(project).resolve())
    paths = [str(p) for p in known_projects() if str(p) != key]
    if present:
        paths.append(key)
    _save(_registry(), sorted(paths))
