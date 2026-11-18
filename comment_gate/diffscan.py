"""Select the comment units Claude added or modified since the baseline."""

import difflib

from . import comments


def changed_lines(old, new):
    """1-based line numbers in `new` that were inserted or replaced."""
    b = new.splitlines()
    if old is None:
        return set(range(1, len(b) + 1))
    sm = difflib.SequenceMatcher(None, old.splitlines(), b, autojunk=False)
    out = set()
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "insert"):
            out.update(range(j1 + 1, j2 + 1))
    return out


def candidates(old, new, lang, extra_protected=()):
    changed = changed_lines(old, new)
    if not changed:
        return []
    out = []
    for u in comments.units(new, lang, group_key=lambda n: n in changed):
        if not u.terminated or not u.body.strip():
            continue
        if not any(u.line <= n <= u.end_line for n in changed):
            continue
        if comments.protected(u, new, extra_protected):
            continue
        out.append(u)
    return out
