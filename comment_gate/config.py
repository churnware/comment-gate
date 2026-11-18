"""Configuration loading: built-in defaults, overlaid by the project's
`.claude/comment-gate.json`, overlaid by environment overrides."""

import json
import os
import re
from pathlib import Path

DEFAULTS = {
    "enabled": True,
    # apply: edit comments in place; suggest: hand edits back to Claude;
    # report: only summarise what would change.
    "mode": "apply",
    "model": None,
    "reasoning_effort": "low",
    "timeout_sec": 90,
    "max_comments": 40,
    "max_chars": 24000,
    "context_lines": 6,
    "include": ["**"],
    "exclude": [
        "**/node_modules/**",
        "**/vendor/**",
        "**/third_party/**",
        "**/dist/**",
        "**/build/**",
        "**/.git/**",
        "**/*.min.*",
        "**/*generated*",
        "**/*.lock",
    ],
    "protected_patterns": [],
    "codex_path": None,
    "notify": True,
}

CONFIG_RELPATH = Path(".claude") / "comment-gate.json"


def state_root():
    return Path(os.environ.get("COMMENT_GATE_HOME") or Path.home() / ".claude" / "comment-gate")


def project_dir(hook_input):
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or hook_input.get("cwd") or os.getcwd())


def load(project):
    cfg = dict(DEFAULTS)
    path = Path(project) / CONFIG_RELPATH
    if path.is_file():
        user = json.loads(path.read_text(encoding="utf-8"))
        unknown = set(user) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"unknown keys in {path}: {', '.join(sorted(unknown))}")
        cfg.update(user)

    env = os.environ.get("COMMENT_GATE", "").strip().lower()
    if env in ("0", "off", "false", "no", "disabled"):
        cfg["enabled"] = False
    if os.environ.get("COMMENT_GATE_MODE"):
        cfg["mode"] = os.environ["COMMENT_GATE_MODE"].strip().lower()
    if cfg["mode"] not in ("apply", "suggest", "report"):
        raise ValueError(f"invalid mode {cfg['mode']!r}")
    return cfg


def _glob_to_regex(pattern):
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z", re.IGNORECASE)


def path_selected(rel_posix, cfg):
    def hit(patterns):
        return any(_glob_to_regex(p).match(rel_posix) for p in patterns)

    return hit(cfg["include"]) and not hit(cfg["exclude"])
