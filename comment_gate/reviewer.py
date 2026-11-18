"""Single batched comment review through `codex exec`."""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCHEMA = {
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
You review source-code comments that an AI coding assistant just wrote or modified.
The goal is high-quality, maintainable comments, not fewer comments for their own sake.

DELETE a comment only when it is:
- redundant: restates what the adjacent code plainly says (e.g. "increment i", "return the result", "import os");
- conversational or session narration: addresses the reader/user or describes the editing process
  ("Here we now...", "As requested, I added...", "Updated to fix the bug", "New helper below");
- misleading or stale relative to the shown code, when it cannot be corrected from the code alone.

REWRITE a comment when its information is worth keeping but it is verbose, padded, hedging or
conversational in tone: keep every fact, cut the words. Also rewrite a misleading comment when the
code shown makes the correct statement unambiguous.

KEEP (omit from output) comments that carry rationale ("why"), constraints, invariants, caveats or
warnings, non-obvious behaviour, units, edge cases, references (issues, specs, links), substantive
TODO/FIXME/HACK notes, API documentation (docstrings, JSDoc/TSDoc, @param/@returns/Args:/Raises:),
section headers that aid navigation in long files, commented-out code, and any tool/compiler directive.

Rules:
- Never invent rationale, intent or facts not present in the comment or evident from the code.
- Never output code or change code. `text` is the new comment body only, without comment markers
  (no //, #, --, /* */, <!-- -->, or quotes). Use "" for delete.
- Preserve the comment's language, terminology, tag syntax (@param, TODO(name):, Args:) and, for API
  docs, the overall structure. Docstrings may be rewritten but never deleted.
- When unsure, keep. Output decisions ONLY for comments you delete or rewrite.
- Do not run commands or read files: everything you need is below.
- Each item gives the comment body and numbered surrounding code; lines marked ">" are the comment.
- `reason`: at most 12 words.

Respond with JSON matching the schema: {"decisions": [{"id", "action", "text", "reason"}]}.
"""

BEGIN, END = "<<<COMMENTS", "COMMENTS>>>"


class ReviewError(Exception):
    pass


def find_codex(cfg):
    """Return the argv prefix that launches codex."""
    override = os.environ.get("COMMENT_GATE_CODEX_CMD")
    if override:
        return json.loads(override)
    if cfg.get("codex_path"):
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


def build_item(cid, rel, lang, unit, text, context_lines):
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
    return {"id": cid, "file": rel, "language": lang, "kind": kind, "comment": unit.body, "context": "\n".join(ctx)}


def build_prompt(items):
    return f"{INSTRUCTIONS}\n{BEGIN}\n{json.dumps(items, indent=1, ensure_ascii=False)}\n{END}\n"


def parse(raw, known_ids):
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
    out = {}
    for d in data["decisions"]:
        if not isinstance(d, dict):
            continue
        cid, action = d.get("id"), d.get("action")
        if cid in known_ids and action in ("delete", "rewrite"):
            out[cid] = {"action": action, "text": str(d.get("text") or ""), "reason": str(d.get("reason") or "")}
    return out


def review(items, cfg):
    """Return {id: decision} for comments to delete or rewrite."""
    argv = find_codex(cfg)
    with tempfile.TemporaryDirectory(prefix="comment-gate-") as tmp:
        tmp = Path(tmp)
        schema, out, work = tmp / "schema.json", tmp / "out.json", tmp / "work"
        work.mkdir()
        schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
        cmd = argv + [
            "exec", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
            "-s", "read-only", "-C", str(work), "--color", "never",
            "--output-schema", str(schema), "-o", str(out),
        ]
        if cfg.get("model"):
            cmd += ["-m", cfg["model"]]
        if cfg.get("reasoning_effort"):
            cmd += ["-c", f'model_reasoning_effort="{cfg["reasoning_effort"]}"']
        cmd.append("-")
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            proc = subprocess.run(
                cmd, input=build_prompt(items), capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=cfg["timeout_sec"], env={**os.environ, "COMMENT_GATE_ACTIVE": "1"},
                **kwargs,
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
