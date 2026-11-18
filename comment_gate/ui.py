"""Terminal presentation: colour, banner, boxes, spinner and prompts.
Colour degrades to plain text when output is not a terminal or NO_COLOR is set."""

import itertools
import os
import re
import shlex
import sys
import threading
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if (getattr(_stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _enable_vt():
    if os.name != "nt":
        return True
    try:
        import ctypes

        k = ctypes.windll.kernel32
        handle, mode = k.GetStdHandle(-11), ctypes.c_uint32()
        if not k.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(k.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False


TTY = sys.stdout.isatty()
COLOR = bool(os.environ.get("FORCE_COLOR")) or (TTY and not os.environ.get("NO_COLOR") and _enable_vt())
TRUECOLOR = COLOR and (
    os.environ.get("COLORTERM") in ("truecolor", "24bit")
    or "WT_SESSION" in os.environ
    or os.environ.get("TERM_PROGRAM") in ("vscode", "WezTerm", "iTerm.app", "ghostty")
)
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _sgr(code, s):
    return f"\x1b[{code}m{s}\x1b[0m" if COLOR else s


def rgb(color, s):
    if not COLOR:
        return s
    r, g, b = color
    if TRUECOLOR:
        return f"\x1b[38;2;{r};{g};{b}m{s}\x1b[0m"
    return f"\x1b[38;5;{16 + 36 * round(r / 51) + 6 * round(g / 51) + round(b / 51)}m{s}\x1b[0m"


def bold(s): return _sgr("1", s)
def dim(s): return _sgr("2", s)
def italic(s): return _sgr("3", s)
def red(s): return _sgr("91", s)
def green(s): return _sgr("92", s)
def yellow(s): return _sgr("93", s)
def cyan(s): return _sgr("96", s)
def magenta(s): return _sgr("95", s)


def visible_len(s):
    return len(ANSI.sub("", s))


# Gradient endpoints shared by the banner and accents.
TEAL, VIOLET = (0, 214, 255), (178, 92, 255)


def _mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def gradient(s, start=TEAL, end=VIOLET):
    if not COLOR:
        return s
    n = max(1, len(s) - 1)
    return "".join(rgb(_mix(start, end, i / n), ch) if ch != " " else ch for i, ch in enumerate(s))


# "Calvin S" figlet glyphs, three rows each.
_GLYPHS = {
    "C": ("╔═╗", "║  ", "╚═╝"), "O": ("╔═╗", "║ ║", "╚═╝"), "M": ("╔╦╗", "║║║", "╩ ╩"),
    "E": ("╔═╗", "║╣ ", "╚═╝"), "N": ("╔╗╔", "║║║", "╝╚╝"), "T": ("╔╦╗", " ║ ", " ╩ "),
    "G": ("╔═╗", "║ ╦", "╚═╝"), "A": ("╔═╗", "╠═╣", "╩ ╩"), " ": ("  ", "  ", "  "),
}

_GATE = ("┌┬┬┬┐", "│││││", "┴┴┴┴┴")


def banner(version, subtitle="keep the why · drop the noise"):
    word = "COMMENT GATE"
    rows = ["".join(_GLYPHS[c][r] for c in word) for r in range(3)]
    print()
    for gate_row, row in zip(_GATE, rows):
        print("  " + rgb(VIOLET, gate_row) + "   " + gradient(row))
    width = len(rows[0]) + len(_GATE[0]) + 3
    tag = f" {subtitle} "
    side = max(2, (width - len(tag)) // 2)
    print("  " + dim("─" * side) + italic(cyan(tag)) + dim("─" * (width - side - len(tag))))
    print("  " + dim(f"v{version} · claude code ⟶ codex review"))
    print()


def box(lines, title=None, color=cyan, pad=1):
    width = max([visible_len(l) for l in lines] + [visible_len(title) + 4 if title else 0]) + pad * 2
    if title:
        print("  " + color("╭─ ") + bold(title) + color(f" {'─' * (width - visible_len(title) - 3)}╮"))
    else:
        print("  " + color(f"╭{'─' * width}╮"))
    for l in lines:
        print("  " + color("│") + " " * pad + l + " " * (width - visible_len(l) - pad) + color("│"))
    print("  " + color(f"╰{'─' * width}╯"))


def ok(msg): print(f"  {green('✔')} {msg}")
def warn(msg): print(f"  {yellow('▲')} {msg}")
def err(msg): print(f"  {red('✖')} {msg}")
def info(msg): print(f"  {cyan('•')} {msg}")
def hint(msg): print(f"    {dim('↳ ' + msg)}")


def heading(title):
    print()
    print("  " + gradient(f"◆ {title}"))


def short(path):
    """Display a path with the home directory abbreviated to ~."""
    p, home = Path(path), Path.home()
    try:
        return "~" + os.sep + str(p.relative_to(home))
    except ValueError:
        return str(p)


class Spinner:
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, text):
        self.text, self._stop, self._t = text, threading.Event(), None

    def __enter__(self):
        if TTY:
            self._t = threading.Thread(target=self._run, daemon=True)
            self._t.start()
        else:
            print(f"  … {self.text}")
        return self

    def _run(self):
        start = time.monotonic()
        for frame in itertools.cycle(self.FRAMES):
            if self._stop.is_set():
                break
            elapsed = dim(f"{time.monotonic() - start:4.1f}s")
            sys.stdout.write(f"\r  {gradient(frame)} {self.text} {elapsed}")
            sys.stdout.flush()
            time.sleep(0.08)

    def __exit__(self, *exc):
        self._stop.set()
        if self._t:
            self._t.join()
            sys.stdout.write("\r\x1b[2K" if COLOR else "\r" + " " * (visible_len(self.text) + 12) + "\r")
            sys.stdout.flush()


def ask(question, default=None):
    suffix = f" {dim(f'[{default}]')}" if default else ""
    try:
        answer = input(f"  {magenta('?')} {bold(question)}{suffix} {cyan('›')} ").strip()
    except EOFError:
        answer = ""
    return answer or (default or "")


def confirm(question, default=False):
    hint_ = "Y/n" if default else "y/N"
    answer = ask(f"{question} {dim('(' + hint_ + ')')}").lower()
    return default if not answer else answer.startswith("y")


def choose(question, options, default=None):
    """options: [(key, label)] -> chosen key."""
    print(f"  {magenta('?')} {bold(question)}")
    for key, label in options:
        marker = cyan("›") if key == default else " "
        print(f"    {marker} {gradient(f'[{key}]')} {label}")
    keys = {k.lower() for k, _ in options}
    while True:
        answer = ask("Choose", default).lower()
        if answer in keys:
            return answer
        err(f"pick one of: {', '.join(k for k, _ in options)}")


def split_paths(raw):
    """Split user input into paths. Accepts quoted paths (as produced by
    drag-and-drop), several paths per line, `~` and environment variables."""
    raw = raw.strip()
    if not raw:
        return []
    try:
        parts = shlex.split(raw, posix=False)
    except ValueError:
        parts = [raw]
    # Unquoted Windows paths containing spaces split badly; fall back to the whole line.
    if len(parts) > 1 and not any(c in raw for c in "\"'") and Path(os.path.expandvars(os.path.expanduser(raw))).exists():
        parts = [raw]
    return [Path(os.path.expandvars(os.path.expanduser(p.strip("\"'")))) for p in parts]


def ask_paths(question, default="."):
    while True:
        paths = split_paths(ask(question, default))
        bad = [p for p in paths if not p.is_dir()]
        if paths and not bad:
            return [p.resolve() for p in paths]
        for p in bad:
            err(f"not a directory: {p}")
        hint("drag folders in, or type paths separated by spaces (quote paths containing spaces)")
