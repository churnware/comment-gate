"""Hook handlers. `snapshot` runs on PreToolUse for file edits; `review` runs
on Stop. Both always exit 0: the gate must never block Claude on failure."""

import hashlib
import json
import os
import sys
import time
import traceback
from pathlib import Path

from . import apply, comments, config, diffscan, reviewer, state

EDIT_TOOLS = {"Write", "Edit", "MultiEdit"}


def _emit(obj):
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def _rel(path, project):
    try:
        return Path(path).resolve().relative_to(Path(project).resolve()).as_posix()
    except ValueError:
        return None


def snapshot(hook):
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
    if rel is None or not comments.language_for(path) or not config.path_selected(rel, cfg):
        return
    state.snapshot(hook.get("session_id"), str(path))


def _collect(session, project, cfg):
    """Return (files, items): per-file review data and the reviewer payload."""
    files, items = {}, []
    for b in state.baselines(session):
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
        for u in units:
            if len(items) >= cfg["max_comments"]:
                break
            item = reviewer.build_item(f"c{len(items) + 1}", rel, lang, u, text, cfg["context_lines"])
            if sum(len(json.dumps(i)) for i in items) + len(json.dumps(item)) > cfg["max_chars"]:
                break
            items.append(item)
            files.setdefault(path, {"rel": rel, "lang": lang, "text": text, "units": {}})["units"][item["id"]] = u
    return files, items


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def _summary(applied, rejected, elapsed, touched):
    deleted = sum(1 for _, d in applied if d["action"] == "delete")
    rewrote = len(applied) - deleted
    parts = []
    if deleted:
        parts.append(f"removed {deleted}")
    if rewrote:
        parts.append(f"rewrote {rewrote}")
    msg = f"Comment gate: {' and '.join(parts) or 'no changes'}"
    if touched:
        msg += f" in {', '.join(sorted(touched))}"
    if rejected:
        msg += f"; skipped {_plural(len(rejected), 'unsafe edit')}"
    return msg + f" ({elapsed:.1f}s)"


def _suggestions(files, decisions):
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


def review(hook):
    if os.environ.get("COMMENT_GATE_ACTIVE"):
        return
    session = hook.get("session_id")
    if hook.get("stop_hook_active"):
        # Second stop after a "suggest" block: Claude's follow-up comment
        # edits are the outcome of the review and must not be re-reviewed.
        if state.flag(session, "suggested"):
            state.flag(session, "suggested", False)
            state.clear_baselines(session)
        return

    project = config.project_dir(hook)
    cfg = config.load(project)
    if not cfg["enabled"]:
        state.clear_baselines(session)
        return
    state.prune_stale()

    files, items = _collect(session, project, cfg)
    if not items:
        state.clear_baselines(session)
        return

    digest = hashlib.sha256(json.dumps(items, sort_keys=True).encode("utf-8")).hexdigest()
    if state.seen(session, digest):
        state.clear_baselines(session)
        return

    t0 = time.monotonic()
    try:
        decisions = reviewer.review(items, cfg)
    except reviewer.ReviewError as e:
        state.clear_baselines(session)
        state.log("review_failed", error=str(e), comments=len(items))
        if cfg["notify"]:
            _emit({"systemMessage": f"Comment gate skipped: {e}"})
        return
    elapsed = time.monotonic() - t0
    state.mark_seen(session, digest)
    state.clear_baselines(session)

    if decisions and cfg["mode"] == "suggest":
        state.flag(session, "suggested", True)
        state.log("suggested", comments=len(items), changes=len(decisions), seconds=round(elapsed, 2))
        _emit({"decision": "block", "reason": _suggestions(files, decisions)})
        return

    applied, rejected, touched, report = [], [], set(), []
    for path, f in files.items():
        decided = [(u, decisions[cid]) for cid, u in f["units"].items() if cid in decisions]
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

    state.log("reviewed", mode=cfg["mode"], comments=len(items), changes=report,
              applied=len(applied), rejected=len(rejected), seconds=round(elapsed, 2))
    if cfg["notify"]:
        msg = _summary(applied, rejected, elapsed, touched)
        if cfg["mode"] == "report":
            msg = msg.replace("Comment gate:", "Comment gate (report only) would have:", 1)
        _emit({"systemMessage": msg})


def main(argv=None):
    argv = sys.argv if argv is None else argv
    event = argv[1] if len(argv) > 1 else ""
    try:
        hook = json.loads(sys.stdin.read() or "{}")
    except ValueError:
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
