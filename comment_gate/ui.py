"""Terminal presentation: colour, banner, boxes, spinner and prompts.
Colour degrades to plain text when output is not a terminal or NO_COLOR is set."""

import itertools
import math
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
VT = TTY and _enable_vt()
COLOR = bool(os.environ.get("FORCE_COLOR")) or (VT and not os.environ.get("NO_COLOR"))
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


# Water palette: muted blues with white highlights. Status colours stay in
# the same family but remain distinguishable (coral for errors, sand for warnings).
DEEP = (46, 74, 112)
TIDE = (78, 118, 160)
MIST = (138, 172, 204)
FOAM = (236, 243, 250)
SAND = (214, 192, 146)
CORAL = (214, 128, 122)


def bold(s): return _sgr("1", s)
def dim(s): return rgb(TIDE, s)
def italic(s): return _sgr("3", s)
def accent(s): return rgb(MIST, s)
def foam(s): return _sgr("1", rgb(FOAM, s)) if COLOR else s
def good(s): return rgb(FOAM, s)
def caution(s): return rgb(SAND, s)
def bad(s): return rgb(CORAL, s)


def visible_len(s):
    return len(ANSI.sub("", s))


def _mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _water(t):
    """Colour for wave height t in [0, 1]: deep water rising to white foam."""
    if t < 0.7:
        return _mix(DEEP, MIST, t / 0.7)
    return _mix(MIST, FOAM, (t - 0.7) / 0.3)


def wave(s, row=0, phase=0.0):
    """Colour `s` as a diagonal swell; crests catch white highlights."""
    if not COLOR:
        return s
    out = []
    for i, ch in enumerate(s):
        if ch == " ":
            out.append(ch)
            continue
        t = 0.5 + 0.5 * math.sin(i * 0.32 - row * 0.8 + phase)
        out.append(rgb(_water(t ** 1.6), ch))
    return "".join(out)


gradient = wave


# "Calvin S" figlet glyphs, three rows each.
_GLYPHS = {
    "C": ("╔═╗", "║  ", "╚═╝"), "O": ("╔═╗", "║ ║", "╚═╝"), "M": ("╔╦╗", "║║║", "╩ ╩"),
    "E": ("╔═╗", "║╣ ", "╚═╝"), "N": ("╔╗╔", "║║║", "╝╚╝"), "T": ("╔╦╗", " ║ ", " ╩ "),
    "G": ("╔═╗", "║ ╦", "╚═╝"), "A": ("╔═╗", "╠═╣", "╩ ╩"), " ": ("  ", "  ", "  "),
}

# A sluice gate standing in water.
_GATE = ("┌┬┬┬┐", "│││││", "≈≈≈≈≈")


def _banner_lines(version, subtitle, phase):
    word = "COMMENT GATE"
    rows = ["".join(_GLYPHS[c][r] for c in word) for r in range(3)]
    lines = []
    for r, (gate_row, row) in enumerate(zip(_GATE, rows)):
        gate = wave(gate_row, r + 2, phase * 1.4) if r == 2 else rgb(DEEP, gate_row)
        lines.append("  " + gate + "   " + wave(row, r, phase))
    width = len(rows[0]) + len(_GATE[0]) + 3
    tag = f"  {subtitle}  "
    side = max(2, (width - len(tag)) // 2)
    ripple = "∼" * width
    lines.append("  " + wave(ripple[:side], 3, phase * 1.2) + italic(foam(tag))
                 + wave(ripple[side + len(tag):], 3, phase * 1.2 + side * 0.32))
    lines.append("  " + dim(f"v{version} · claude code ⟶ codex review"))
    return lines


def banner(version, subtitle="drop the noise", animate=True):
    print()
    animate = animate and COLOR and TTY and not os.environ.get("COMMENT_GATE_NO_ANIM")
    frames = 14 if animate else 1
    for f in range(frames):
        lines = _banner_lines(version, subtitle, f * 0.45)
        if f:
            sys.stdout.write(f"\x1b[{len(lines)}A")
        sys.stdout.write("\n".join(l + "\x1b[K" if COLOR else l for l in lines) + "\n")
        sys.stdout.flush()
        if animate and f < frames - 1:
            time.sleep(0.045)
    print()


def box(lines, title=None, color=accent, pad=1):
    width = max([visible_len(l) for l in lines] + [visible_len(title) + 4 if title else 0]) + pad * 2
    if title:
        print("  " + color("╭─ ") + foam(title) + color(f" {'─' * (width - visible_len(title) - 3)}╮"))
    else:
        print("  " + color(f"╭{'─' * width}╮"))
    for l in lines:
        print("  " + color("│") + " " * pad + l + " " * (width - visible_len(l) - pad) + color("│"))
    print("  " + color(f"╰{'─' * width}╯"))


def ok(msg): print(f"  {good('✔')} {msg}")
def warn(msg): print(f"  {caution('▲')} {msg}")
def err(msg): print(f"  {bad('✖')} {msg}")
def info(msg): print(f"  {accent('∘')} {msg}")
def hint(msg): print(f"    {dim('↳ ' + msg)}")


def heading(title):
    print()
    print("  " + wave("≈ ", 1) + foam(title))


def clear():
    """Start a fresh page: wipe the screen and scrollback."""
    if VT:
        sys.stdout.write("\x1b[2J\x1b[3J\x1b[H")
        sys.stdout.flush()


def pause(msg="enter to return to the menu"):
    try:
        input(f"\n  {dim('↵ ' + msg)} ")
    except EOFError:
        pass


def short(path):
    """Return a path string with the home directory abbreviated to ~."""
    p, home = Path(path), Path.home()
    try:
        return "~" + os.sep + str(p.relative_to(home))
    except ValueError:
        return str(p)


class Spinner:
    WIDTH = 6

    def __init__(self, text):
        self.text, self._stop, self._t = text, threading.Event(), None

    def __enter__(self):
        if TTY:
            self._t = threading.Thread(target=self._run, daemon=True)
            self._t.start()
        else:
            print(f"  ≈ {self.text}")
        return self

    def _run(self):
        start = time.monotonic()
        for tick in itertools.count():
            if self._stop.is_set():
                break
            elapsed = dim(f"{time.monotonic() - start:4.1f}s")
            swell = wave("≈" * self.WIDTH, 0, -tick * 0.5)
            sys.stdout.write(f"\r  {swell} {self.text} {elapsed}")
            sys.stdout.flush()
            time.sleep(0.08)

    def __exit__(self, *exc):
        self._stop.set()
        if self._t:
            self._t.join()
            sys.stdout.write("\r\x1b[2K" if COLOR else "\r" + " " * (visible_len(self.text) + self.WIDTH + 10) + "\r")
            sys.stdout.flush()


def ask(question, default=None):
    suffix = f" {dim(f'[{default}]')}" if default else ""
    try:
        answer = input(f"  {accent('?')} {foam(question)}{suffix} {accent('›')} ").strip()
    except EOFError:
        answer = ""
    return answer or (default or "")


def confirm(question, default=False):
    hint_ = "Y/n" if default else "y/N"
    answer = ask(f"{question} {dim('(' + hint_ + ')')}").lower()
    return default if not answer else answer.startswith("y")


def choose(question, options, default=None):
    """options: [(key, label)] -> chosen key."""
    print(f"  {accent('?')} {foam(question)}")
    for i, (key, label) in enumerate(options):
        marker = foam("›") if key == default else " "
        print(f"    {marker} {wave(f'[{key}]', i)} {label}")
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
    # If an unquoted Windows path splits at spaces, use the whole line only if it resolves to an existing path.
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
