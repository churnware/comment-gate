"""Single batched comment review through `codex exec`."""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Collection
from pathlib import Path
from typing import Any, Literal, TypedDict

from .comments import Unit
from .config import Config

Action = Literal["delete", "rewrite"]


class Item(TypedDict):
    """One comment as the reviewer sees it."""

    id: str
    file: str
    language: str
    kind: str
    comment: str
    context: str


class Decision(TypedDict):
    action: Action
    # The new comment body; "" for a delete.
    text: str
    reason: str


SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "action": {"type": "string", "enum": ["keep", "delete", "rewrite"]},
                    "text": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "action", "text", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["decisions"],
    "additionalProperties": False,
}

INSTRUCTIONS = """\
Review source-code comments that an AI coding assistant just wrote or modified.
The goal is maintainability. Detailed explanations are worth keeping when they
make complex code easier to understand. When unsure, keep the comment.

DELETE a comment only when it:
- restates what the adjacent code plainly says;
- narrates the editing session ("As requested...", "Updated to fix the bug");
- retells the implementation or data flow without adding insight;
- justifies an ordinary construct without identifying a meaningful trade-off,
  requirement or constraint;
- divides otherwise readable code with unnecessary headings, steps or separators;
- is demonstrably misleading or stale and cannot be corrected from the shown code.

REWRITE when a comment contains useful information buried in padding,
conversational phrasing or redundant narration. Also rewrite a stale or
misleading comment when the shown code makes the correction unambiguous.
Preserve substantive details and meaningful uncertainty. Use concise, natural
engineering language. Sentence fragments are fine.

Example:
  Original:
  "Each file is an animated WebP that plays the banner's wave once and rests on the
  last frame. The frames come from ui._banner_lines itself, parsed out of its ANSI
  output, so the image follows any change to the banner's art, subtitle or colours."

  Rewrite:
  "Derive frames from the live banner to avoid maintaining a separate artwork source."

  This rewrite assumes the omitted details are apparent from the code. If playing
  once and holding the last frame is a required behaviour, preserve that detail.

KEEP comments that explain:
- rationale, trade-offs, constraints, invariants, caveats or warnings;
- non-obvious behaviour, units, edge cases or why a simpler approach fails.

Keep diagrams, tables, worked examples and derivations that clarify complex
relationships. Keep useful section headers, references (issues, specs, links),
substantive TODO/FIXME/HACK notes and API documentation.

Rules:
- Leave commented-out code and tool/compiler directives untouched.
- You may rewrite docstrings, but never delete them.
- Never invent rationale, intent or facts. Use only the comment and shown code.
- Respect local style. Do not rewrite solely for punctuation, capitalisation
  or sentence structure.
- Preserve the comment's language, terminology, tag syntax and meaningful
  formatting, including diagram alignment and API documentation structure.
- Do not run commands or read files. Each item supplies the comment body and
  numbered surrounding code. Lines marked ">" are the comment.

Return JSON only, with a `decisions` array. Include one entry for each deletion
or rewrite. Omit comments you keep. If all comments are kept, return {"decisions": []}.

Each entry has four fields:
- `id`: copy the input item's id.
- `action`: "delete" or "rewrite".
- `text`: for a rewrite, the new comment body only, without surrounding comment
  markers or docstring delimiters. For a deletion, use "". Never output or change code.
- `reason`: at most 12 words.
"""

BEGIN, END = "<<<COMMENTS", "COMMENTS>>>"


class ReviewError(Exception):
    pass


def find_codex(cfg: Config) -> list[str]:
    """Return the argv prefix that launches codex."""
    override = os.environ.get("COMMENT_GATE_CODEX_CMD")
    if override:
        argv = json.loads(override)
        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
            raise ReviewError("COMMENT_GATE_CODEX_CMD must be a JSON list of strings")
        return argv
    if cfg["codex_path"]:
        return [cfg["codex_path"]]
    on_path = shutil.which("codex")
    if on_path:
        return [on_path]
    # The VS Code extension bundles a standalone codex binary.
    exe = "codex.exe" if os.name == "nt" else "codex"
    pattern = str(Path.home() / ".vscode*" / "extensions" / "openai.chatgpt-*" / "bin" / "*" / exe)
    found = glob.glob(pattern)
    if found:
        return [max(found, key=os.path.getmtime)]
    raise ReviewError("codex not found (set codex_path in .claude/comment-gate.json)")


def build_item(cid: str, rel: str, lang: str, unit: Unit, text: str, context_lines: int) -> Item:
    lines = text.replace("\r", "").split("\n")
    lo = max(1, unit.line - context_lines)
    hi = min(len(lines), unit.end_line + context_lines)
    ctx = []
    for n in range(lo, hi + 1):
        mark = ">" if unit.line <= n <= unit.end_line else " "
        line = lines[n - 1]
        ctx.append(f"{n:>5}{mark} {line[:200]}")
    kind = {"doc": "docstring"}.get(unit.kind, unit.kind)
    if unit.kind != "doc" and unit.code_before:
        kind = "trailing " + kind
    lang = f"{unit.lang} in {lang}" if unit.lang else lang
    return {"id": cid, "file": rel, "language": lang, "kind": kind, "comment": unit.body, "context": "\n".join(ctx)}


def build_prompt(items: list[Item]) -> str:
    return f"{INSTRUCTIONS}\n{BEGIN}\n{json.dumps(items, indent=1, ensure_ascii=False)}\n{END}\n"


def parse(raw: str, known_ids: Collection[str]) -> dict[str, Decision]:
    raw = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if m:
        raw = m.group(1)
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise ReviewError(f"reviewer returned invalid JSON: {raw[:120]!r}") from e
    if not isinstance(data, dict) or not isinstance(data.get("decisions"), list):
        raise ReviewError("reviewer response missing 'decisions'")
    out: dict[str, Decision] = {}
    for d in data["decisions"]:
        if not isinstance(d, dict):
            continue
        cid, action = d.get("id"), d.get("action")
        if isinstance(cid, str) and cid in known_ids and (action == "delete" or action == "rewrite"):
            out[cid] = {"action": action, "text": str(d.get("text") or ""), "reason": str(d.get("reason") or "")}
    return out


def review(items: list[Item], cfg: Config) -> dict[str, Decision]:
    """Return {id: decision} for comments to delete or rewrite."""
    argv = find_codex(cfg)
    with tempfile.TemporaryDirectory(prefix="comment-gate-") as tmp_name:
        tmp = Path(tmp_name)
        schema, out, work = tmp / "schema.json", tmp / "out.json", tmp / "work"
        work.mkdir()
        schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
        cmd = argv + [
            "exec", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
            "-s", "read-only", "-C", str(work), "--color", "never",
            "--output-schema", str(schema), "-o", str(out),
        ]
        if cfg["model"]:
            cmd += ["-m", cfg["model"]]
        if cfg["reasoning_effort"]:
            cmd += ["-c", f'model_reasoning_effort="{cfg["reasoning_effort"]}"']
        cmd.append("-")
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW
        try:
            proc = subprocess.run(
                cmd, input=build_prompt(items), capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=cfg["timeout_sec"], env={**os.environ, "COMMENT_GATE_ACTIVE": "1"},
                creationflags=flags,
            )
        except subprocess.TimeoutExpired as e:
            raise ReviewError(f"codex timed out after {cfg['timeout_sec']}s") from e
        except OSError as e:
            raise ReviewError(f"could not launch codex: {e}") from e
        if proc.returncode != 0:
            tail = [l for l in (proc.stderr or proc.stdout or "").strip().splitlines() if l.strip()]
            raise ReviewError(f"codex exited {proc.returncode}: {tail[-1][:200] if tail else 'no output'}")
        raw = out.read_text(encoding="utf-8") if out.exists() else proc.stdout
        return parse(raw, {i["id"] for i in items})
