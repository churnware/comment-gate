"""Install, remove or inspect the comment gate hooks for one project.

    python install.py <project>              # hooks -> <project>/.claude/settings.local.json
    python install.py <project> --shared     # hooks -> <project>/.claude/settings.json
    python install.py <project> --uninstall
    python install.py <project> --status
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from comment_gate import config, reviewer  # noqa: E402

HOOK = ROOT / "hook.py"
OURS = re.compile(r"hook\.py\"?\s+(snapshot|review)\s*$")


def _command(event):
    return f'"{Path(sys.executable).as_posix()}" "{HOOK.as_posix()}" {event}'


def _settings_path(project, shared):
    return Path(project) / ".claude" / ("settings.json" if shared else "settings.local.json")


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


def install(project, shared):
    path = _settings_path(project, shared)
    settings = _strip_ours(_load(path))
    cfg = config.load(project)
    hooks = settings.setdefault("hooks", {})
    hooks.setdefault("PreToolUse", []).append({
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [{"type": "command", "command": _command("snapshot"), "timeout": 10}],
    })
    hooks.setdefault("Stop", []).append({
        "hooks": [{"type": "command", "command": _command("review"), "timeout": int(cfg["timeout_sec"]) + 30}],
    })
    _save(path, settings)
    print(f"hooks written to {path}")

    cfg_path = Path(project) / config.CONFIG_RELPATH
    if not cfg_path.exists():
        defaults = {k: config.DEFAULTS[k] for k in ("enabled", "mode", "model", "reasoning_effort", "timeout_sec")}
        _save(cfg_path, defaults)
        print(f"default config written to {cfg_path}")


def uninstall(project):
    for shared in (False, True):
        path = _settings_path(project, shared)
        if path.is_file():
            _save(path, _strip_ours(_load(path)))
            print(f"hooks removed from {path}")


def status(project):
    installed = []
    for shared in (False, True):
        path = _settings_path(project, shared)
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if "hook.py" in text and "review" in text:
            installed.append(path.name)
    print(f"installed in: {', '.join(installed) or 'nowhere'}")
    cfg = config.load(project)
    print("config:", json.dumps(cfg, indent=2))
    try:
        print("codex:", " ".join(reviewer.find_codex(cfg)))
    except reviewer.ReviewError as e:
        print("codex: NOT FOUND -", e)
    print("log:", config.state_root() / "log.jsonl")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", type=Path)
    ap.add_argument("--shared", action="store_true", help="write to settings.json instead of settings.local.json")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    project = a.project.resolve()
    if not project.is_dir():
        ap.error(f"not a directory: {project}")
    if a.status:
        status(project)
    elif a.uninstall:
        uninstall(project)
    else:
        install(project, a.shared)


if __name__ == "__main__":
    main()
