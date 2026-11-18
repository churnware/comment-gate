"""Add or remove the gate's hooks in a project's Claude Code settings, and
remember which projects have it installed."""

import json
import re
import sys
from pathlib import Path

from . import config

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hook.py"
OURS = re.compile(r"hook\.py\"?\s+(snapshot|review)\s*$")
SETTINGS = {False: "settings.local.json", True: "settings.json"}


def _command(event):
    return f'"{Path(sys.executable).as_posix()}" "{HOOK.as_posix()}" {event}'


def settings_path(project, shared):
    return Path(project) / ".claude" / SETTINGS[shared]


def _load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _strip_ours(settings):
    hooks = settings.get("hooks", {})
    for event in list(hooks):
        groups = []
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


def git_root(path):
    for p in [Path(path), *Path(path).parents]:
        if (p / ".git").exists():
            return p
    return None


def install(project, shared=False, mode=None):
    """Install hooks; returns (settings_path, config_path, config_created)."""
    project = Path(project)
    cfg_path = project / config.CONFIG_RELPATH
    created = not cfg_path.exists()
    if created:
        _save(cfg_path, {k: config.DEFAULTS[k] for k in ("enabled", "mode", "model", "reasoning_effort", "timeout_sec")})
    if mode:
        data = _load(cfg_path)
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
    hooks.setdefault("Stop", []).append({
        "hooks": [{"type": "command", "command": _command("review"), "timeout": int(cfg["timeout_sec"]) + 30}],
    })
    _save(path, settings)
    remember(project, True)
    return path, cfg_path, created


def uninstall(project):
    """Remove our hooks from both settings files; returns the files changed."""
    changed = []
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


def installed_in(project):
    out = []
    for shared in (False, True):
        path = settings_path(project, shared)
        try:
            settings = _load(path)
        except (OSError, ValueError):
            continue
        cmds = [h.get("command", "") for gs in settings.get("hooks", {}).values() for g in gs for h in g.get("hooks", [])]
        if any(OURS.search(c) for c in cmds):
            out.append(path.name)
    return out


def _registry():
    return config.state_root() / "projects.json"


def known_projects():
    try:
        paths = json.loads(_registry().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [Path(p) for p in paths if Path(p).is_dir()]


def remember(project, present):
    key = str(Path(project).resolve())
    paths = [str(p) for p in known_projects() if str(p) != key]
    if present:
        paths.append(key)
    _save(_registry(), sorted(paths))
