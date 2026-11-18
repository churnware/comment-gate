"""Language-aware comment extraction.

Lexers return raw comment spans (character offsets into the original text,
CRLF preserved). `units()` groups adjacent full-line comments into reviewable
units and computes their marker-free body text."""

import ast
import inspect
import io
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path


class Unsupported(Exception):
    pass


@dataclass(frozen=True)
class Spec:
    line: tuple = ()
    blocks: tuple = ()
    quotes: tuple = ('"', "'")
    backtick: bool = False
    regex: bool = False
    triple: bool = False
    # Hash comments in shell/YAML only start at a word boundary (`$#` is code).
    line_needs_ws: bool = False


C = Spec(line=("//",), blocks=(("/*", "*/"),), triple=True)
FAMILIES = {
    "c": C,
    "js": Spec(line=("//",), blocks=(("/*", "*/"),), backtick=True, regex=True),
    "go": Spec(line=("//",), blocks=(("/*", "*/"),), backtick=True),
    "css": Spec(blocks=(("/*", "*/"),)),
    "hash": Spec(line=("#",), line_needs_ws=True),
    "ps1": Spec(line=("#",), blocks=(("<#", "#>"),), line_needs_ws=True),
    "sql": Spec(line=("--",), blocks=(("/*", "*/"),), quotes=("'", '"')),
    "lua": Spec(line=("--",), blocks=(("--[[", "]]"),)),
    "hs": Spec(line=("--",), blocks=(("{-", "-}"),), quotes=('"',)),
    "markup": Spec(blocks=(("<!--", "-->"),), quotes=()),
}

EXTENSIONS = {
    "python": "py pyi pyw",
    "js": "js mjs cjs jsx ts tsx mts cts",
    "c": "c h cc cpp cxx hpp hh hxx cs java kt kts swift scala dart php groovy m mm proto rs zig v sol scss less",
    "go": "go",
    "css": "css",
    "hash": "sh bash zsh fish rb yaml yml toml r pl pm tf hcl nix cmake mk dockerfile makefile gitignore ini conf",
    "ps1": "ps1 psm1 psd1",
    "sql": "sql",
    "lua": "lua",
    "hs": "hs elm",
    "markup": "html htm xml xaml svg vue svelte",
}
EXT_TO_LANG = {ext: lang.strip() for lang, exts in EXTENSIONS.items() for ext in exts.split()}


def language_for(path):
    p = Path(path)
    ext = p.suffix[1:].lower() if p.suffix else p.name.lower()
    return EXT_TO_LANG.get(ext)


@dataclass
class Raw:
    kind: str  # "line" | "block" | "doc"
    start: int
    end: int
    opener: str
    closer: str = ""
    prefix: str = ""


@dataclass
class Unit:
    kind: str
    opener: str
    closer: str
    prefix: str
    start: int
    end: int
    line: int  # 1-based, inclusive
    end_line: int
    indent: str
    code_before: bool
    code_after: bool
    parts: list = field(default_factory=list)
    body: str = ""
    terminated: bool = True

    @property
    def standalone(self):
        return not self.code_before and not self.code_after


def _skip_string(text, i, q):
    n = len(text)
    if q != "`" and text.startswith(q * 3, i):
        j = text.find(q * 3, i + 3)
        return n if j < 0 else j + 3
    j = i + 1
    while j < n:
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == q:
            return j + 1
        if ch == "\n" and q != "`":
            return j
        j += 1
    return n


def _skip_regex(text, i):
    n, j, in_class = len(text), i + 1, False
    while j < n:
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "\n":
            return j
        if in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif ch == "/":
            return j + 1
        j += 1
    return n


_REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%<>~^") | {""}


def _lex_generic(text, spec):
    out, i, n, prev = [], 0, len(text), ""
    blocks = sorted(spec.blocks, key=lambda b: -len(b[0]))
    quotes = spec.quotes + (("`",) if spec.backtick else ())
    while i < n:
        hit = None
        for op, cl in blocks:
            if text.startswith(op, i):
                j = text.find(cl, i + len(op))
                hit = Raw("block", i, n if j < 0 else j + len(cl), op, cl)
                break
        if hit is None:
            for m in spec.line:
                if text.startswith(m, i) and (not spec.line_needs_ws or i == 0 or text[i - 1] in " \t\r\n"):
                    j = text.find("\n", i)
                    end = n if j < 0 else j
                    if text[end - 1 : end] == "\r":
                        end -= 1
                    hit = Raw("line", i, end, m)
                    break
        if hit is not None:
            out.append(hit)
            i = hit.end
            continue
        ch = text[i]
        if ch in quotes:
            i = _skip_string(text, i, ch)
            prev = ch
            continue
        if spec.regex and ch == "/" and prev in _REGEX_PRECEDERS:
            i = _skip_regex(text, i)
            prev = "/"
            continue
        if not ch.isspace():
            prev = ch
        i += 1
    return out


def _line_offsets(text):
    offs, pos = [0], 0
    for line in text.split("\n"):
        pos += len(line) + 1
        offs.append(pos)
    return offs


def _lex_python(text):
    offs = _line_offsets(text)
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                s = offs[tok.start[0] - 1] + tok.start[1]
                out.append(Raw("line", s, s + len(tok.string), "#"))
    except (tokenize.TokenError, SyntaxError) as e:
        raise Unsupported(f"python tokenize failed: {e}") from e

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    lines = text.split("\n")

    def char_off(lineno, col_bytes):
        line = lines[lineno - 1]
        return offs[lineno - 1] + len(line.encode("utf-8")[:col_bytes].decode("utf-8", "ignore"))

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.body or not isinstance(node.body[0], ast.Expr):
            continue
        val = node.body[0].value
        if not (isinstance(val, ast.Constant) and isinstance(val.value, str)):
            continue
        s, e = char_off(val.lineno, val.col_offset), char_off(val.end_lineno, val.end_col_offset)
        raw = text[s:e]
        m = re.match(r"([rRuU]?)(\"\"\"|'''|\"|')", raw)
        # Implicitly concatenated or otherwise unusual literals are left alone.
        if not m or not raw.endswith(m.group(2)) or len(raw) < len(m.group(0)) + len(m.group(2)):
            continue
        try:
            if ast.literal_eval(raw) != val.value:
                continue
        except (ValueError, SyntaxError):
            continue
        out.append(Raw("doc", s, e, m.group(2), m.group(2), m.group(1)))
    out.sort(key=lambda r: r.start)
    return out


def lex(text, lang):
    if lang == "python":
        return _lex_python(text)
    spec = FAMILIES.get(lang)
    if spec is None:
        raise Unsupported(f"no lexer for {lang}")
    return _lex_generic(text, spec)


def _line_marker(text, raw):
    """Extend a line marker over doc-style repeats: `///`, `//!`, `###`, `---`."""
    m, j = raw.opener, raw.start + len(raw.opener)
    while j < raw.end and (text[j] == m[-1] or (m.startswith("//") and text[j] == "!" and len(m) == 2)):
        m += text[j]
        j += 1
    return m


def _block_opener(text, raw):
    if raw.opener == "/*":
        nxt = text[raw.start + 2 : raw.start + 4]
        if nxt[:1] in ("*", "!") and nxt != "*/":
            return "/" + "*" + nxt[0]
    return raw.opener


def _block_body(inner):
    lines = [l.rstrip() for l in inner.replace("\r", "").split("\n")]
    rest = [l for l in lines[1:] if l.strip()]
    star = bool(rest) and all(l.lstrip().startswith("*") for l in rest)
    out = [lines[0].strip()]
    tail = []
    for l in lines[1:]:
        if star:
            s = l.lstrip()[1:] if l.lstrip().startswith("*") else l.lstrip()
            tail.append(s[1:] if s.startswith(" ") else s)
        else:
            tail.append(l)
    tail = inspect.cleandoc("\n".join(["x"] + tail)).split("\n")[1:] if tail else []
    out += tail
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def units(text, lang, group_key=None):
    """Comment units in `text`. Adjacent full-line comments are merged only
    when `group_key(line)` agrees, so old and new comments stay separate."""
    raws = lex(text, lang)
    group_key = group_key or (lambda _line: None)
    offs = _line_offsets(text)
    n = len(text)

    def line_of(pos):
        lo, hi = 0, len(offs) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if offs[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    def line_end(lineno):
        e = min(offs[lineno] - 1, n) if lineno < len(offs) else n
        return e - 1 if text[e - 1 : e] == "\r" else e

    out = []
    for r in raws:
        line, end_line = line_of(r.start), line_of(max(r.start, r.end - 1))
        ls = offs[line - 1]
        before = text[ls : r.start]
        after = text[r.end : line_end(end_line)]
        code_before = bool(before.strip())
        indent = re.match(r"[ \t]*", before).group()
        opener = r.opener
        if r.kind == "line":
            opener = _line_marker(text, r)
        elif r.kind == "block":
            opener = _block_opener(text, r)
        prev = out[-1] if out else None
        if (
            r.kind == "line"
            and not code_before
            and prev is not None
            and prev.kind == "line"
            and not prev.code_before
            and prev.opener == opener
            and prev.indent == indent
            and prev.end_line + 1 == line
            and group_key(prev.end_line) == group_key(line)
        ):
            prev.parts.append(r)
            prev.end, prev.end_line = r.end, end_line
            continue
        out.append(
            Unit(r.kind, opener, r.closer, r.prefix, r.start, r.end, line, end_line, indent,
                 code_before, bool(after.strip()), [r],
                 terminated=r.kind == "line" or text.endswith(r.closer, 0, r.end))
        )

    for u in out:
        if u.kind == "line":
            body = []
            for p in u.parts:
                s = text[p.start + len(u.opener) : p.end]
                body.append((s[1:] if s.startswith(" ") else s).rstrip())
            u.body = "\n".join(body)
        elif u.kind == "block":
            inner = text[u.start + len(u.opener) : u.end - len(u.closer)] if u.terminated else ""
            u.body = _block_body(inner)
        else:
            raw = text[u.start : u.end]
            u.body = inspect.cleandoc(raw[len(u.prefix) + len(u.opener) : -len(u.closer)].replace("\r", ""))
    return out


# Comments that tools, compilers or legal requirements depend on. Units
# matching any of these (in raw text or body) are never sent for review.
DIRECTIVES = [
    r"^#!", r"coding[:=]", r"-\*-.*-\*-", r"\bvim?:\s*(set\b|\w+=)",
    r"\beslint(-disable|-enable|-env)?\b", r"\bjshint\b", r"^\s*globals?\s+[\w$]+\s*(?:[:,]|$)",
    r"@ts-(ignore|expect-error|nocheck|check)\b",
    r"\bnoqa\b", r"\btype:\s*ignore", r"\bpragma\b", r"\bpylint:", r"\bmypy:", r"\bpyright:", r"\bruff:",
    r"\bfmt:\s*(off|on|skip)\b", r"\bisort:", r"\bnosec\b", r"\bno\s*cover\b",
    r"^\s*go:", r"^\s*\+build\b", r"\bnolint\b", r"\bclang-format\s+(off|on)\b",
    r"^\s*#?(?:region|endregion)\b", r"\bprettier-ignore\b", r"\b(istanbul|c8|v8)\s+ignore\b",
    r"@(jsx|jsxImportSource|jsxFrag|flow|license|preserve|vite-ignore|refresh|vitest-environment|jest-environment)\b",
    r"webpack[A-Z]\w*\s*:", r"[#@]\s*sourceMappingURL", r"\bsourceURL=", r"\bSPDX-License-Identifier\b",
    r"\bcopyright\b", r"\(c\)\s*\d{4}", r"\blicen[cs]ed?\b", r"\bshellcheck\s+(disable|source)", r"\brubocop:",
    r"\bdeno-lint-ignore", r"\bbiome-ignore", r"\bcspell:", r"\bstylelint-", r"\btslint:",
    r"\bfrozen_string_literal:", r"\bswiftlint:", r"\bReSharper\s+disable",
    r"\bauto-?generated\b", r"\bdo not edit\b",
]


def protected(unit, text, extra_patterns=()):
    raw = text[unit.start : unit.end]
    flags = re.IGNORECASE | re.MULTILINE
    return any(
        re.search(pat, raw, flags) or re.search(pat, unit.body, flags)
        for pat in list(DIRECTIVES) + list(extra_patterns)
    )
