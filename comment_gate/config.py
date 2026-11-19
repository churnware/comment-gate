"""Configuration loading: built-in defaults, overlaid by the global
`config.json` in the state directory, then the project's
`.claude/comment-gate.json`, then environment overrides."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, TypeAlias, TypedDict, cast

Mode: TypeAlias = Literal["apply", "suggest", "report"]


class Config(TypedDict):
    enabled: bool
    mode: Mode
    model: str | None
    reasoning_effort: str | None
    timeout_sec: int
    max_comments: int
    max_chars: int
    max_batches: int
    context_lines: int
    include: list[str]
    exclude: list[str]
    protected_patterns: list[str]
    codex_path: str | None
    notify: bool


# A value as a config file holds it, before it is known to suit its key.
Setting: TypeAlias = bool | int | str | list[str] | None
# One config layer: some or all of the keys of `Config`.
Layer: TypeAlias = dict[str, Setting]

DEFAULTS: Config = {
    "enabled": True,
    # apply: edit comments in place; suggest: hand edits back to Claude;
    # report: only summarise what would change.
    "mode": "apply",
    "model": None,
    "reasoning_effort": "low",
    "timeout_sec": 90,
    "max_comments": 40,
    "max_chars": 24000,
    # Codex calls per review, run in parallel; each takes up to max_comments
    # and max_chars. Files beyond that wait for the next review.
    "max_batches": 6,
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
MODES: tuple[Mode, ...] = ("apply", "suggest", "report")
MODE_HELP: dict[str, str] = {
    "apply": "edit comments in place after each turn",
    "suggest": "hand the edits back to Claude to apply",
    "report": "only show what would change",
}


def state_root() -> Path:
    return Path(os.environ.get("COMMENT_GATE_HOME") or Path.home() / ".claude" / "comment-gate")


def project_dir(hook_input: Mapping[str, Any]) -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or hook_input.get("cwd") or os.getcwd())


def file_path(project: Path | None = None) -> Path:
    """The project's config file, or the global one when `project` is None."""
    return state_root() / "config.json" if project is None else Path(project) / CONFIG_RELPATH


def read_file(project: Path | None = None) -> Layer:
    """A config file as written, without defaults, other layers or environment overrides."""
    path = file_path(project)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return cast(Layer, data)


def write_file(project: Path | None, data: Layer) -> None:
    path = file_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def inherited(project: Path | None = None) -> Layer:
    """The settings a layer starts from: defaults, plus the global file for a project."""
    cfg = cast(Layer, dict(DEFAULTS))
    if project is not None:
        cfg.update(_checked(None))
    return cfg


def _checked(project: Path | None) -> Layer:
    user = read_file(project)
    unknown = set(user) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown keys in {file_path(project)}: {', '.join(sorted(unknown))}")
    return user


def load(project: Path | None) -> Config:
    cfg = inherited(project)
    cfg.update(_checked(project))

    env = os.environ.get("COMMENT_GATE", "").strip().lower()
    if env in ("0", "off", "false", "no", "disabled"):
        cfg["enabled"] = False
    if os.environ.get("COMMENT_GATE_MODE"):
        cfg["mode"] = os.environ["COMMENT_GATE_MODE"].strip().lower()
    if cfg["mode"] not in MODES:
        raise ValueError(f"invalid mode {cfg['mode']!r}")
    return cast(Config, cfg)


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
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


def path_selected(rel_posix: str, cfg: Config) -> bool:
    def hit(patterns: list[str]) -> bool:
        return any(_glob_to_regex(p).match(rel_posix) for p in patterns)

    return hit(cfg["include"]) and not hit(cfg["exclude"])
