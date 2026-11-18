"""Turn reviewer decisions into text edits and apply them, rejecting any edit
that would change anything other than comment text."""

import ast
import re

from . import comments

DOC_PLACEHOLDER = "\x00DOC\x00"


def _body_lines(body, unit):
    lines = [l.rstrip() for l in body.replace("\r", "").split("\n")]
    # Models sometimes echo the comment marker; drop it when every line has it.
    if unit.kind == "line" and lines and all(l.lstrip().startswith(unit.opener) or not l.strip() for l in lines):
        lines = [l.lstrip()[len(unit.opener):].removeprefix(" ") if l.strip() else "" for l in lines]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _full_line_span(text, start, end):
    s = text.rfind("\n", 0, start) + 1
    e = text.find("\n", end)
    return s, len(text) if e < 0 else e + 1


def _delete(text, unit):
    if unit.kind == "doc":
        return None
    if unit.standalone:
        s, e = _full_line_span(text, unit.start, unit.end)
        return s, e, ""
    s = unit.start
    while s > 0 and text[s - 1] in " \t":
        s -= 1
    if unit.code_before and not unit.code_after:
        return s, unit.end, ""
    if unit.code_before:
        # Comment sits between two tokens: never glue them together.
        nxt = text[unit.end : unit.end + 1]
        if s == unit.start and nxt not in (" ", "\t"):
            return unit.start, unit.end, " "
        if nxt in (" ", "\t") or not (nxt.isalnum() or nxt == "_"):
            return s, unit.end, ""
        return unit.start, unit.end, ""
    e = unit.end
    while e < len(text) and text[e] in " \t":
        e += 1
    return unit.start, e, ""


def edit_for(text, unit, action, body):
    """Return (start, end, replacement) for one decision, or None if unsafe."""
    if action == "keep":
        return None
    lines = _body_lines(body, unit) if action == "rewrite" else []
    if action == "delete" or not lines:
        return _delete(text, unit)
    if action != "rewrite":
        return None

    nl = "\r\n" if "\r\n" in text else "\n"
    ind = unit.indent
    if unit.kind == "line":
        if unit.code_before:
            return unit.start, unit.end, f"{unit.opener} {' '.join(l.strip() for l in lines)}"
        rendered = (nl + ind).join(unit.opener + (" " + l if l else "") for l in lines)
        return unit.start, unit.end, rendered

    if unit.kind == "block":
        if any(unit.closer in l for l in lines):
            return None
        if not unit.standalone or (unit.line == unit.end_line and len(lines) == 1):
            return unit.start, unit.end, f"{unit.opener} {' '.join(l.strip() for l in lines)} {unit.closer}"
        if unit.opener.startswith("/*"):
            inner = "".join(f"{ind} *{' ' + l if l else ''}{nl}" for l in lines)
            return unit.start, unit.end, f"{unit.opener}{nl}{inner}{ind} {unit.closer}"
        inner = "".join(f"{ind}  {l}{nl}" if l else nl for l in lines)
        return unit.start, unit.end, f"{unit.opener}{nl}{inner}{ind}{unit.closer}"

    q = unit.opener
    joined = "\n".join(lines)
    if q in joined or joined.endswith(q[0]) or ("\\" in joined and "r" not in unit.prefix.lower()):
        return None
    if len(lines) == 1 or len(q) == 1:
        return unit.start, unit.end, f"{unit.prefix}{q}{' '.join(l.strip() for l in lines)}{q}"
    rest = "".join(f"{ind}{l}{nl}" if l else nl for l in lines[1:])
    return unit.start, unit.end, f"{unit.prefix}{q}{lines[0]}{nl}{rest}{ind}{q}"


_TOKEN = re.compile(r"\w+|\S")


def skeleton(text, lang):
    """Per-line (indent, tokens) of the source with comments removed and
    docstrings masked. Blank lines and intra-line spacing are ignored, but
    word tokens stay distinct, so `a b` and `ab` never compare equal."""
    parts, pos = [], 0
    for r in comments.lex(text, lang):
        parts.append(text[pos : r.start])
        parts.append(DOC_PLACEHOLDER if r.kind == "doc" else " ")
        pos = r.end
    parts.append(text[pos:])
    out = []
    for line in "".join(parts).replace("\r", "").split("\n"):
        if line.strip():
            indent = line[: len(line) - len(line.lstrip())]
            out.append((indent, tuple(_TOKEN.findall(line))))
    return out


def _parses(text):
    try:
        ast.parse(text)
        return True
    except SyntaxError:
        return False


def apply_decisions(text, lang, decided):
    """Apply [(unit, decision)] bottom-up, keeping only code-preserving edits.

    Returns (new_text, applied, rejected) where the lists hold (unit, decision)."""
    base_skel = skeleton(text, lang)
    base_parses = lang == "python" and _parses(text)
    applied, rejected = [], []
    cur = text
    for unit, dec in sorted(decided, key=lambda ud: ud[0].start, reverse=True):
        edit = edit_for(cur, unit, dec["action"], dec.get("text", ""))
        if edit is None:
            rejected.append((unit, dec))
            continue
        s, e, repl = edit
        candidate = cur[:s] + repl + cur[e:]
        try:
            ok = skeleton(candidate, lang) == base_skel
        except comments.Unsupported:
            ok = False
        if ok and base_parses:
            ok = _parses(candidate)
        if ok:
            cur = candidate
            applied.append((unit, dec))
        else:
            rejected.append((unit, dec))
    return cur, applied, rejected
