"""Terminal presentation: colour, banner, boxes, spinner and prompts.
Colour degrades to plain text when output is not a terminal or NO_COLOR is set."""

from __future__ import annotations

import io
import itertools
import math
import os
import re
import shlex
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TypeAlias

# An (r, g, b) colour, each channel 0-255.
Color: TypeAlias = tuple[int, int, int]
Style: TypeAlias = Callable[[str], str]

for _stream in (sys.stdout, sys.stderr):
    if isinstance(_stream, io.TextIOWrapper) and (_stream.encoding or "").lower().replace("-", "") != "utf8":
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except ValueError:
            pass


def _enable_vt() -> bool:
    if sys.platform != "win32":
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


def _sgr(code: str, s: str) -> str:
    return f"\x1b[{code}m{s}\x1b[0m" if COLOR else s


def _color_code(color: Color, layer: int = 38) -> str:
    """SGR parameters for a foreground (38) or background (48) colour."""
    r, g, b = color
    if TRUECOLOR:
        return f"{layer};2;{r};{g};{b}"
    return f"{layer};5;{16 + 36 * round(r / 51) + 6 * round(g / 51) + round(b / 51)}"


def rgb(color: Color, s: str) -> str:
    return f"\x1b[{_color_code(color)}m{s}\x1b[0m" if COLOR else s


# Water palette: muted blues with white highlights. Status colours stay in
# the same family but remain distinguishable (coral for errors, sand for warnings).
DEEP: Color = (46, 74, 112)
TIDE: Color = (78, 118, 160)
MIST: Color = (138, 172, 204)
FOAM: Color = (236, 243, 250)
SAND: Color = (214, 192, 146)
CORAL: Color = (214, 128, 122)


def bold(s: str) -> str: return _sgr("1", s)
def dim(s: str) -> str: return rgb(TIDE, s)
def italic(s: str) -> str: return _sgr("3", s)
def accent(s: str) -> str: return rgb(MIST, s)
def foam(s: str) -> str: return _sgr("1", rgb(FOAM, s)) if COLOR else s
def good(s: str) -> str: return rgb(FOAM, s)
def caution(s: str) -> str: return rgb(SAND, s)
def bad(s: str) -> str: return rgb(CORAL, s)


def chip(s: str) -> str:
    """Render a primary control as a lit keycap, or in brackets when colour is
    disabled.
    """
    if not COLOR:
        return f"[{s}]"
    return f"\x1b[1;{_color_code(FOAM)};{_color_code(TIDE, 48)}m {s} \x1b[0m"


def visible_len(s: str) -> int:
    return len(ANSI.sub("", s))


def fit(s: str, width: int) -> str:
    """Cut a coloured string to `width` visible characters."""
    if visible_len(s) <= width:
        return s
    out: list[str] = []
    seen = 0
    for part in re.split(f"({ANSI.pattern})", s):
        if ANSI.fullmatch(part):
            out.append(part)
            continue
        out.append(part[:max(0, width - 1 - seen)])
        seen += len(out[-1])
    return "".join(out) + "…" + ("\x1b[0m" if COLOR else "")


def _mix(a: Color, b: Color, t: float) -> Color:
    red, green, blue = (round(x + (y - x) * t) for x, y in zip(a, b))
    return red, green, blue


def _water(t: float) -> Color:
    """Colour for wave height t in [0, 1]: deep water rising to white foam."""
    if t < 0.7:
        return _mix(DEEP, MIST, t / 0.7)
    return _mix(MIST, FOAM, (t - 0.7) / 0.3)


def wave(s: str, row: int = 0, phase: float = 0.0) -> str:
    """Colour `s` as a diagonal swell; crests catch white highlights."""
    if not COLOR:
        return s
    out: list[str] = []
    for i, ch in enumerate(s):
        if ch == " ":
            out.append(ch)
            continue
        t = 0.5 + 0.5 * math.sin(i * 0.32 - row * 0.8 + phase)
        out.append(rgb(_water(t ** 1.6), ch))
    return "".join(out)


gradient = wave


# "Calvin S" figlet glyphs, three rows each.
_GLYPHS: dict[str, tuple[str, str, str]] = {
    "C": ("╔═╗", "║  ", "╚═╝"), "O": ("╔═╗", "║ ║", "╚═╝"), "M": ("╔╦╗", "║║║", "╩ ╩"),
    "E": ("╔═╗", "║╣ ", "╚═╝"), "N": ("╔╗╔", "║║║", "╝╚╝"), "T": ("╔╦╗", " ║ ", " ╩ "),
    "G": ("╔═╗", "║ ╦", "╚═╝"), "A": ("╔═╗", "╠═╣", "╩ ╩"), " ": ("  ", "  ", "  "),
}

# A sluice gate standing in water.
_GATE = ("┌┬┬┬┐", "│││││", "≈≈≈≈≈")


def _banner_lines(version: str, subtitle: str, phase: float) -> list[str]:
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


FRAMES, FRAME_SEC = 7, 0.09

 
def animated() -> bool:
    return COLOR and TTY and not os.environ.get("COMMENT_GATE_NO_ANIM")


def ease_out(t: float) -> float:
    """Quadratic ease-out: motion starts fast and settles into its final position."""
    return 1 - (1 - t) ** 2


def banner(version: str, subtitle: str = "hush, my sweet llm child", animate: bool = True) -> None:
    print()
    animate = animate and animated()
    frames = FRAMES if animate else 1
    for f in range(frames):
        t = f / (frames - 1) if frames > 1 else 1.0
        lines = _banner_lines(version, subtitle, 6.6 * ease_out(t))
        if f:
            sys.stdout.write(f"\x1b[{len(lines)}A")
        sys.stdout.write("\n".join(l + "\x1b[K" if COLOR else l for l in lines) + "\n")
        sys.stdout.flush()
        if animate and f < frames - 1:
            time.sleep(FRAME_SEC)
    print()


# The water wheel's rim is fixed while its spokes and paddles turn around the
# hub. A flume feeds the stream from the left, and the bottom paddle dips
# into it.
_RIM = (
    "        ╭───────╮        ",
    "      ╭─╯       ╰─╮      ",
    "     ╭╯           ╰╮     ",
    "     │             │     ",
    "     │             │     ",
    "     │             │     ",
    "     ╰╮           ╭╯     ",
    "      ╰─╮       ╭─╯      ",
    "        ╰───────╯        ",
)
WHEEL_WIDTH, WHEEL_HEIGHT = len(_RIM[0]), len(_RIM) + 4
_HUB_ROW, _HUB_COL, _WATERLINE = 6, WHEEL_WIDTH // 2, WHEEL_HEIGHT - 2


def _spoke_char(a: float) -> str:
    d = math.degrees(a) % 180
    if d < 22.5 or d >= 157.5:
        return "─"
    if d < 67.5:
        return "╱"
    if d < 112.5:
        return "│"
    return "╲"


def _polar(r: float, a: float) -> tuple[int, int]:
    # Terminal cells are about twice as tall as they are wide.
    return round(_HUB_ROW - r * math.sin(a)), round(_HUB_COL + 2 * r * math.cos(a))


def water_wheel(angle: float, running: bool = True) -> list[str]:
    """The wheel turned to `angle` radians (positive is anticlockwise) as
    coloured lines. A wheel that is not running is drawn in still, dark water."""
    cell: tuple[str, Color | None] = (" ", None)
    grid = [[cell] * WHEEL_WIDTH for _ in range(WHEEL_HEIGHT)]

    def put(r: int, c: int, ch: str, color: Color, over: bool = False) -> None:
        if 0 <= r < WHEEL_HEIGHT and 0 <= c < WHEEL_WIDTH and (over or grid[r][c][0] == " "):
            grid[r][c] = (ch, color)

    for i, row in enumerate(_RIM):
        for j, ch in enumerate(row):
            if ch != " ":
                put(i + 2, j, ch, MIST if running else TIDE)
    for k in range(4):
        a = angle + k * math.pi / 2
        (r0, c0), (r1, c1) = _polar(0.9, a), _polar(3.4, a)
        n = max(abs(c1 - c0), abs(r1 - r0), 1)
        for i in range(n + 1):
            put(round(r0 + (r1 - r0) * i / n), round(c0 + (c1 - c0) * i / n), _spoke_char(a), TIDE)
    for k in range(8):
        a = angle + k * math.pi / 4
        r, c = _polar(5.0, a)
        lit = 0.5 + 0.5 * math.sin(a)
        shade = FOAM if r >= _WATERLINE else _water(lit) if running else _mix(DEEP, TIDE, lit)
        put(r, c, "█", shade, over=True)
        if abs(math.cos(a)) > 0.6:
            put(r, c + (1 if math.cos(a) > 0 else -1), "█", shade, over=True)
    put(_HUB_ROW, _HUB_COL, "◉", FOAM if running else MIST, over=True)
    for c in range(9):
        put(0, c, "═", DEEP)
    put(0, 9, "╗", DEEP)
    if running:
        put(1, 10, "╲", FOAM)

    lines: list[str] = []
    for r, cells in enumerate(grid):
        out: list[str] = []
        for c, (ch, color) in enumerate(cells):
            if ch == " " and r >= _WATERLINE:
                ch = "≈" if r == _WATERLINE else "∼"
                # The stream flows with the wheel, so it only moves while the wheel turns.
                t = 0.5 + 0.5 * math.sin(c * 0.32 - r * 0.8 - angle * 2.5)
                color = _water(t ** 1.6) if running else _mix(DEEP, TIDE, t)
            out.append(rgb(color, ch) if color else ch)
        lines.append("".join(out))
    return lines


def box(lines: list[str], title: str | None = None, color: Style = accent, pad: int = 1) -> None:
    width = max([visible_len(l) for l in lines] + [visible_len(title) + 4 if title else 0]) + pad * 2
    if title:
        print("  " + color("╭─ ") + foam(title) + color(f" {'─' * (width - visible_len(title) - 3)}╮"))
    else:
        print("  " + color(f"╭{'─' * width}╮"))
    for l in lines:
        print("  " + color("│") + " " * pad + l + " " * (width - visible_len(l) - pad) + color("│"))
    print("  " + color(f"╰{'─' * width}╯"))


def ok(msg: str) -> None: print(f"  {good('✔')} {msg}")
def warn(msg: str) -> None: print(f"  {caution('▲')} {msg}")
def err(msg: str) -> None: print(f"  {bad('✖')} {msg}")
def info(msg: str) -> None: print(f"  {accent('∘')} {msg}")
def hint(msg: str) -> None: print(f"    {dim('↳ ' + msg)}")


def heading(title: str) -> None:
    print()
    print("  " + wave("≈ ", 1) + foam(title))


def clear() -> None:
    """Start a fresh page: wipe the screen and scrollback."""
    if VT:
        sys.stdout.write("\x1b[2J\x1b[3J\x1b[H")
        sys.stdout.flush()


class Back(Exception):
    """Esc in a menu or prompt: return to the previous menu."""


def interactive_input() -> bool:
    return VT and sys.stdin.isatty()


def pause(msg: str = "any key to return to the menu") -> None:
    if not interactive_input():
        try:
            input(f"\n  {dim('↵ ' + msg)} ")
        except EOFError:
            pass
        return
    sys.stdout.write(f"\n  {dim('↵ ' + msg)} ")
    sys.stdout.flush()
    with KeyReader() as kb:
        kb.read()
    print()


def short(path: str | Path) -> str:
    """Return a path string with the home directory abbreviated to ~."""
    p, home = Path(path), Path.home()
    try:
        return "~" + os.sep + str(p.relative_to(home))
    except ValueError:
        return str(p)


class Spinner:
    WIDTH = 6

    def __init__(self, text: str) -> None:
        self.text, self._stop = text, threading.Event()
        self._t: threading.Thread | None = None

    def __enter__(self) -> Spinner:
        if TTY:
            self._t = threading.Thread(target=self._run, daemon=True)
            self._t.start()
        else:
            print(f"  ≈ {self.text}")
        return self

    def _run(self) -> None:
        start = time.monotonic()
        for tick in itertools.count():
            if self._stop.is_set():
                break
            elapsed = dim(f"{time.monotonic() - start:4.1f}s")
            swell = wave("≈" * self.WIDTH, 0, -tick * 0.5)
            sys.stdout.write(f"\r  {swell} {self.text} {elapsed}")
            sys.stdout.flush()
            time.sleep(0.08)

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._t:
            self._t.join()
            sys.stdout.write("\r\x1b[2K" if COLOR else "\r" + " " * (visible_len(self.text) + self.WIDTH + 10) + "\r")
            sys.stdout.flush()


def _read_line(prompt: str) -> str:
    """input() that raises Back on Esc. Editing is append and backspace only."""
    sys.stdout.write(prompt)
    sys.stdout.flush()
    text = ""
    with KeyReader() as kb:
        while True:
            key = kb.read()
            if key == "esc":
                print()
                raise Back
            if key == "enter":
                print()
                return text
            if key == "backspace":
                if text:
                    text = text[:-1]
                    sys.stdout.write("\b \b")
            elif len(key) == 1 and key.isprintable():
                text += key
                sys.stdout.write(key)
            sys.stdout.flush()


def ask(question: str, default: str | None = None) -> str:
    suffix = f" {dim(f'[{default}]')}" if default else ""
    prompt = f"  {accent('?')} {foam(question)}{suffix} {accent('›')} "
    try:
        answer = (_read_line(prompt) if interactive_input() else input(prompt)).strip()
    except EOFError:
        answer = ""
    return answer or (default or "")


def confirm(question: str, default: bool = False) -> bool:
    hint_ = "Y/n" if default else "y/N"
    answer = ask(f"{question} {dim('(' + hint_ + ')')}").lower()
    return default if not answer else answer.startswith("y")


class KeyReader:
    """Unbuffered, unechoed keypresses for the duration of a `with` block."""

    _fd: int
    _saved: list[Any]

    def __enter__(self) -> KeyReader:
        if sys.platform != "win32":
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        return self

    def __exit__(self, *exc: object) -> None:
        if sys.platform != "win32":
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)

    def ready(self, timeout: float) -> bool:
        """Wait up to `timeout` seconds; True as soon as a key is waiting."""
        if sys.platform == "win32":
            import msvcrt

            deadline = time.monotonic() + timeout
            while not msvcrt.kbhit():
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.005)
            return True
        import select

        return bool(select.select([self._fd], [], [], timeout)[0])

    def read(self) -> str:
        """One keypress: 'up', 'down', 'left', 'right', 'enter', 'esc',
        'backspace' or the character typed."""
        if sys.platform == "win32":
            import msvcrt

            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                return {"H": "up", "P": "down", "K": "left", "M": "right",
                        "S": "backspace"}.get(msvcrt.getwch(), "")
        else:
            raw = os.read(self._fd, 1)
            if raw[0] >= 0xC0:
                raw += os.read(self._fd, 1 if raw[0] < 0xE0 else 2 if raw[0] < 0xF0 else 3)
            ch = raw.decode(errors="ignore")
            if ch == "\x1b":
                if not self.ready(0.05):
                    return "esc"
                seq = os.read(self._fd, 2).decode(errors="ignore")
                if seq == "[3" and self.ready(0.05):
                    os.read(self._fd, 1)  # the "~" that ends the Delete key's sequence
                    return "backspace"
                return {"[A": "up", "OA": "up", "[B": "down", "OB": "down",
                        "[D": "left", "OD": "left", "[C": "right", "OC": "right"}.get(seq, "")
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x1b":
            return "esc"
        if ch in ("\x08", "\x7f"):
            return "backspace"
        return ch


class Trail:
    """A selection marker's trail fades over a few frames on the rows
    it just left.
    """

    FRAME_SEC = 0.035

    def __init__(self) -> None:
        # Maps recently vacated rows to trail intensity (1 -> 0).
        self.rows: dict[int, float] = {}

    @property
    def fading(self) -> bool:
        return bool(self.rows)

    def move(self, left: int, to: int) -> None:
        if COLOR and to != left:
            self.rows[left] = 1.0
        self.rows.pop(to, None)

    def step(self) -> None:
        for i in list(self.rows):
            self.rows[i] -= 0.2
            if self.rows[i] <= 0:
                del self.rows[i]

    def clear(self) -> None:
        self.rows.clear()

    def marker(self, row: int, selected: bool) -> str:
        if selected:
            return foam("›")
        if row in self.rows:
            return rgb(_mix(DEEP, MIST, self.rows[row]), "›")
        return " "


def _choose_with_arrows(options: Sequence[tuple[str, str]], default: str | None) -> str:
    keys = [k.lower() for k, _ in options]
    index = keys.index(default.lower()) if default and default.lower() in keys else 0
    trail = Trail()

    def draw(redraw: bool) -> None:
        if redraw:
            sys.stdout.write(f"\x1b[{len(options)}A")
        for i, (key, label) in enumerate(options):
            sys.stdout.write(f"\r    {trail.marker(i, i == index)} {wave(f'[{key}]', i)} {label}\x1b[K\n")
        sys.stdout.flush()

    def move(to: int) -> None:
        nonlocal index
        trail.move(index, to)
        index = to
        draw(True)

    sys.stdout.write("\x1b[?25l")
    try:
        with KeyReader() as kb:
            draw(False)
            while True:
                # Fade the trail between keypresses, but never make a keypress wait on it.
                while trail.fading and not kb.ready(Trail.FRAME_SEC):
                    trail.step()
                    draw(True)
                key = kb.read()
                if key == "up":
                    move((index - 1) % len(options))
                elif key == "down":
                    move((index + 1) % len(options))
                elif key == "enter":
                    break
                elif key in ("esc", "backspace", "left"):
                    raise Back
                elif key.lower() in keys:
                    move(keys.index(key.lower()))
                    break
            trail.clear()
            draw(True)
            return keys[index]
    finally:
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


def choose(question: str, options: Sequence[tuple[str, str]], default: str | None = None) -> str:
    """options: [(key, label)] -> chosen key. Esc raises Back."""
    print(f"  {accent('?')} {foam(question)}")
    if interactive_input():
        return _choose_with_arrows(options, default)
    for i, (key, label) in enumerate(options):
        marker = foam("›") if key == default else " "
        print(f"    {marker} {wave(f'[{key}]', i)} {label}")
    keys = {k.lower() for k, _ in options}
    while True:
        answer = ask("Choose", default).lower()
        if answer in keys:
            return answer
        err(f"pick one of: {', '.join(k for k, _ in options)}")


def split_paths(raw: str) -> list[Path]:
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


def ask_paths(question: str, default: str = ".") -> list[Path]:
    while True:
        paths = split_paths(ask(question, default))
        bad = [p for p in paths if not p.is_dir()]
        if paths and not bad:
            return [p.resolve() for p in paths]
        for p in bad:
            err(f"not a directory: {p}")
        hint("drag folders in, or type paths separated by spaces (quote paths containing spaces)")
