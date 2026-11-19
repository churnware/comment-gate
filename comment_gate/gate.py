"""Hook handlers. `snapshot` runs on PreToolUse for file edits; `review` runs
on Stop for the main agent's edits and on SubagentStop for each subagent's.
Both always exit 0: the gate must never block Claude on failure."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import traceback
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypedDict

from . import apply, comments, config, diffscan, installer, reviewer, state
from .apply import Decided
from .comments import Unit
from .config import Config
from .reviewer import Decision, Item

EDIT_TOOLS = {"Write", "Edit", "MultiEdit"}


class HookInput(TypedDict, total=False):
    """The fields of Claude Code's hook input that the gate reads."""

    session_id: str
    # Set only in hook input from inside a subagent.
    agent_id: str
    hook_event_name: str
    tool_name: str
    tool_input: dict[str, Any]
    cwd: str
    stop_hook_active: bool


class FileReview(TypedDict):
    rel: str
    lang: str
    # The text the comments were found in, to detect edits during the review.
    text: str
    units: dict[str, Unit]


def _emit(obj: dict[str, str]) -> None:
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def _rel(path: str | Path, project: Path) -> str | None:
    try:
        return Path(path).resolve().relative_to(Path(project).resolve()).as_posix()
    except ValueError:
        return None


def _owner(hook: HookInput) -> str | None:
    # Hook input from inside a subagent carries its `agent_id`; the main
    # agent's carries none.
    return hook.get("agent_id") or None


def snapshot(hook: HookInput) -> None:
    if os.environ.get("COMMENT_GATE_ACTIVE") or hook.get("tool_name") not in EDIT_TOOLS:
        return
    project = config.project_dir(hook)
    cfg = config.load(project)
    if not cfg["enabled"]:
        return
    raw_path = (hook.get("tool_input") or {}).get("file_path")
    if not raw_path:
        return
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(hook.get("cwd") or project) / path
    rel = _rel(path, project)
    lang = comments.language_for(path)
    if rel is None:
        if lang:
            state.note_outside(hook.get("session_id"), str(path))
        return
    if not lang or not config.path_selected(rel, cfg):
        return
    state.snapshot(hook.get("session_id"), str(path), _owner(hook))


def _pack(batches: Sequence[list[Item]], items: list[Item], cfg: Config) -> list[list[Item]] | None:
    """Return a copy of `batches` with `items` appended, opening new batches
    as the per-call caps fill up, or None if that needs more than `max_batches`."""
    out = [list(b) for b in batches] or [[]]
    sizes = [sum(len(json.dumps(i)) for i in b) for b in out]
    for item in items:
        size = len(json.dumps(item))
        if out[-1] and (len(out[-1]) >= cfg["max_comments"] or sizes[-1] + size > cfg["max_chars"]):
            out.append([])
            sizes.append(0)
        out[-1].append(item)
        sizes[-1] += size
    return out if len(out) <= max(1, cfg["max_batches"]) else None


def _collect(
    baselines: list[state.Baseline], project: Path, cfg: Config
) -> tuple[dict[str, FileReview], list[list[Item]], dict[str, int], int]:
    """Return (files, batches, deferred, dropped).

    `files` maps each path to its review data and `batches` holds one reviewer
    payload per Codex call. A file that no longer fits in the batches is
    deferred whole ({path: comment count}), so its baseline stays valid for
    the next review. `dropped` counts comments of a single file too large for
    every batch combined; those are never reviewed."""
    files: dict[str, FileReview] = {}
    batches: list[list[Item]] = []
    deferred: dict[str, int] = {}
    dropped, next_id = 0, 1
    for b in sorted(baselines, key=lambda b: os.path.normcase(b["path"])):
        path = b["path"]
        lang = comments.language_for(path)
        rel = _rel(path, project)
        if not lang or rel is None or not config.path_selected(rel, cfg):
            continue
        try:
            text = state.read_text(path)
        except (ValueError, UnicodeDecodeError, OSError):
            continue
        if text is None or text == b["content"]:
            continue
        try:
            units = diffscan.candidates(b["content"], text, lang, cfg["protected_patterns"])
        except comments.Unsupported as e:
            state.log("skip_file", path=rel, reason=str(e))
            continue
        built = [(reviewer.build_item(f"c{next_id + k}", rel, lang, u, text, cfg["context_lines"]), u)
                 for k, u in enumerate(units)]
        next_id += len(built)
        packed = _pack(batches, [i for i, _ in built], cfg)
        if packed is None and batches:
            deferred[path] = len(built)
            continue
        while packed is None and built:
            built.pop()
            dropped += 1
            packed = _pack([], [i for i, _ in built], cfg)
        if not built or packed is None:
            continue
        batches = packed
        f = files.setdefault(path, {"rel": rel, "lang": lang, "text": text, "units": {}})
        for item, u in built:
            f["units"][item["id"]] = u
    return files, [b for b in batches if b], deferred, dropped


def _review_batches(batches: list[list[Item]], cfg: Config) -> tuple[dict[str, Decision], list[str]]:
    """Review every batch in parallel. Returns (decisions, errors); raises
    ReviewError only when every call failed."""
    if len(batches) == 1:
        return reviewer.review(batches[0], cfg), []
    with ThreadPoolExecutor(max_workers=len(batches)) as pool:
        futures = [pool.submit(reviewer.review, b, cfg) for b in batches]
    decisions: dict[str, Decision] = {}
    errors: list[str] = []
    for f in futures:
        try:
            decisions.update(f.result())
        except reviewer.ReviewError as e:
            errors.append(str(e))
    if len(errors) == len(batches):
        raise reviewer.ReviewError(errors[0])
    return decisions, errors


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _summary(applied: list[Decided], rejected: list[Decided], elapsed: float, touched: set[str], reviewed: int) -> str:
    deleted = sum(1 for _, d in applied if d["action"] == "delete")
    rewrote = len(applied) - deleted
    parts: list[str] = []
    if deleted:
        parts.append(f"removed {deleted}")
    if rewrote:
        parts.append(f"rewrote {rewrote}")
    msg = f"Comment gate: {' and '.join(parts) or 'no changes'}"
    if touched:
        msg += f" in {', '.join(sorted(touched))}"
    if rejected:
        msg += f"; skipped {_plural(len(rejected), 'unsafe edit')}"
    return msg + f" ({_plural(reviewed, 'comment')} reviewed, {elapsed:.1f}s)"


def _suggestions(files: dict[str, FileReview], decisions: dict[str, Decision]) -> str:
    lines = [
        "Independent comment review (comment gate) suggests these comment-only edits. "
        "Apply them exactly as described without changing any code, then finish. "
        "If one is clearly wrong, skip it.",
    ]
    for path, f in files.items():
        for cid, u in f["units"].items():
            d = decisions.get(cid)
            if not d:
                continue
            where = f"{f['rel']}:{u.line}"
            old = u.body.replace("\n", " / ")
            if d["action"] == "delete":
                lines.append(f"- {where} DELETE comment \"{old}\" ({d['reason']})")
            else:
                new = d["text"].replace("\n", " / ")
                lines.append(f"- {where} REWRITE \"{old}\" -> \"{new}\" ({d['reason']})")
    return "\n".join(lines)


def _notes(deferred: dict[str, int], dropped: int, outside: list[str], failed: int, batches: int) -> list[str]:
    """Extra clauses for the summary: anything that was left unreviewed."""
    out: list[str] = []
    if deferred:
        n = sum(deferred.values())
        out.append(f"deferred {_plural(n, 'comment')} in {_plural(len(deferred), 'file')} to the next review")
    if dropped:
        out.append(f"{_plural(dropped, 'comment')} over the review limits not reviewed")
    if failed:
        out.append(f"{failed} of {batches} review calls failed")
    if outside:
        names = ", ".join(os.path.basename(p) for p in outside[:3]) + (", ..." if len(outside) > 3 else "")
        out.append(f"ignored {_plural(len(outside), 'file')} outside the project ({names})")
    return out


def _owned(session: str | None, agent: str | None, everything: bool = False) -> list[state.Baseline]:
    return [b for b in state.baselines(session) if everything or b.get("agent_id") == agent]


def review(hook: HookInput) -> None:
    if os.environ.get("COMMENT_GATE_ACTIVE"):
        return
    session = hook.get("session_id")
    agent = _owner(hook)
    if hook.get("hook_event_name") == "SubagentStop" and not agent:
        # Without an id the subagent's edits can't be told apart from the
        # main agent's, which may still be in progress; Stop reviews them.
        return
    suggested = f"suggested-{agent or 'main'}"
    if hook.get("stop_hook_active"):
        # Second stop after a "suggest" block: Claude's follow-up comment
        # edits are the outcome of the review and must not be re-reviewed.
        if state.flag(session, suggested):
            state.flag(session, suggested, False)
            state.clear_baselines(session, [b["path"] for b in _owned(session, agent) if not b.get("deferred")])
        return

    project = config.project_dir(hook)
    cfg = config.load(project)
    # Older installs have no SubagentStop hook, so Stop must take every edit.
    owned = _owned(session, agent, everything=not agent and not installer.has_subagent_hook(project))
    if not cfg["enabled"]:
        state.clear_baselines(session, [b["path"] for b in owned])
        return
    state.prune_stale()

    outside = state.take_outside(session)
    files, batches, deferred, dropped = _collect(owned, project, cfg)
    done = [b["path"] for b in owned if b["path"] not in deferred]
    for path in deferred:
        state.defer(session, path)
    items = [i for b in batches for i in b]
    if dropped or deferred:
        state.log("over_limit", comments=len(items), deferred=len(deferred),
                  deferred_comments=sum(deferred.values()), dropped=dropped)
    if not items:
        state.clear_baselines(session, done)
        notes = _notes(deferred, dropped, outside, 0, 0)
        if notes and cfg["notify"]:
            _emit({"systemMessage": "Comment gate: " + "; ".join(notes)})
        return

    digest = hashlib.sha256(json.dumps(items, sort_keys=True).encode("utf-8")).hexdigest()
    if state.seen(session, digest):
        state.clear_baselines(session, done)
        return

    t0 = time.monotonic()
    try:
        decisions, errors = _review_batches(batches, cfg)
    except reviewer.ReviewError as e:
        state.clear_baselines(session, done)
        state.log("review_failed", error=str(e), comments=len(items))
        if cfg["notify"]:
            _emit({"systemMessage": "; ".join([f"Comment gate skipped: {e}"] + _notes(deferred, dropped, outside, 0, 0))})
        return
    elapsed = time.monotonic() - t0
    for error in errors:
        state.log("review_failed", error=error, comments=len(items), partial=True)
    state.mark_seen(session, digest)
    state.clear_baselines(session, done)
    notes = _notes(deferred, dropped, outside, len(errors), len(batches))

    if decisions and cfg["mode"] == "suggest":
        state.flag(session, suggested, True)
        state.log("suggested", comments=len(items), changes=len(decisions), seconds=round(elapsed, 2))
        _emit({"decision": "block", "reason": _suggestions(files, decisions)})
        return

    applied: list[Decided] = []
    rejected: list[Decided] = []
    touched: set[str] = set()
    report: list[dict[str, object]] = []
    for path, f in files.items():
        decided: list[Decided] = [(u, decisions[cid]) for cid, u in f["units"].items() if cid in decisions]
        if not decided:
            continue
        for u, d in decided:
            report.append({"file": f["rel"], "line": u.line, "action": d["action"], "old": u.body,
                           "new": d["text"], "reason": d["reason"]})
        if cfg["mode"] == "report":
            applied += decided
            touched.add(f["rel"])
            continue
        try:
            if state.read_text(path) != f["text"]:
                state.log("skip_file", path=f["rel"], reason="changed during review")
                rejected += decided
                continue
            new_text, ok, bad = apply.apply_decisions(f["text"], f["lang"], decided)
            if ok:
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(new_text)
                touched.add(f["rel"])
            applied += ok
            rejected += bad
        except (OSError, ValueError, comments.Unsupported) as e:
            state.log("apply_failed", path=f["rel"], error=str(e))
            rejected += decided

    state.log("reviewed", mode=cfg["mode"], comments=len(items), batches=len(batches), changes=report,
              applied=len(applied), rejected=len(rejected), seconds=round(elapsed, 2))
    if cfg["notify"]:
        msg = _summary(applied, rejected, elapsed, touched, len(items))
        if cfg["mode"] == "report":
            msg = msg.replace("Comment gate:", "Comment gate (report only) would have:", 1)
        _emit({"systemMessage": "; ".join([msg] + notes)})


def main(argv: Sequence[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    event = argv[1] if len(argv) > 1 else ""
    hook: HookInput
    try:
        hook = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        hook = {}
    if not isinstance(hook, dict):
        hook = {}
    try:
        if event == "snapshot":
            snapshot(hook)
        elif event == "review":
            review(hook)
        else:
            print("usage: hook.py snapshot|review  (reads hook JSON on stdin)", file=sys.stderr)
    except Exception as e:
        state.log("error", hook_event=event, error=repr(e), trace=traceback.format_exc(limit=6))
        if event == "review":
            _emit({"systemMessage": f"Comment gate error (ignored): {e}"})
    return 0
