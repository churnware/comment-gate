"""Interactive editor for a project's `.claude/comment-gate.json`, drawn beside
a water wheel that churns as the editor opens and turns with every change."""

from __future__ import annotations

import math
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Literal, TypeAlias

from . import config, installer, models, ui
from .config import Setting
from .models import Model

# Offered when Codex hasn't cached its model list yet.
EFFORTS: tuple[str, ...] = ("minimal", "low", "medium", "high")

FieldKind: TypeAlias = Literal["bool", "choice", "model", "int", "text"]
# What Picker.handle returns: a value picked, a cancel, or None to keep going.
PickerResult: TypeAlias = tuple[Literal["pick"], str | None] | Literal["cancel"] | None


class Field:
    def __init__(self, key: str, kind: FieldKind, help: str, choices: tuple[str, ...] = (), step: int = 1,
                 low: int = 0, unset: str = "") -> None:
        self.key, self.kind, self.help = key, kind, help
        self.choices, self.step, self.low, self.unset = choices, step, low, unset


FIELDS: tuple[Field, ...] = (
    Field("enabled", "bool", "master switch for this project"),
    Field("mode", "choice", "what happens to the edits", choices=config.MODES),
    Field("model", "model", "codex model", unset="codex default"),
    Field("reasoning_effort", "choice", "passed to codex as model_reasoning_effort", choices=EFFORTS),
    Field("timeout_sec", "int", "seconds before a review gives up; the Stop hook waits 30 longer", step=15, low=15),
    Field("max_comments", "int", "most comments in one codex call", step=5, low=1),
    Field("max_chars", "int", "most characters in one codex call", step=2000, low=1000),
    Field("max_batches", "int", "most parallel codex calls per review; files beyond them wait", low=1),
    Field("context_lines", "int", "lines of surrounding code sent with each comment"),
    Field("notify", "bool", "show a one-line summary after each review"),
    Field("codex_path", "text", "explicit codex binary; when unset, PATH then the VS Code extension", unset="auto"),
)

NAME_WIDTH, VALUE_WIDTH = 17, 26
RIGHT_WIDTH = 2 + NAME_WIDTH + 1 + VALUE_WIDTH + 4
# Turns of the wheel, in radians; negative is clockwise.
OPEN_TURN, MOVE_TURN, CHANGE_TURN, SAVE_TURN = -math.pi / 2, math.pi / 16, math.pi / 8, -math.pi / 2


class Wheel:
    """The wheel's angle, eased towards its target with the banner's frame pacing."""

    def __init__(self) -> None:
        self.angle = self._start = self._target = 0.0
        self._frame = ui.FRAMES - 1

    @property
    def spinning(self) -> bool:
        return self._frame < ui.FRAMES - 1

    def kick(self, turn: float) -> None:
        # Pending motion is capped so a burst of keypresses can't spin the wheel
        # fast enough for the paddles to appear to run backwards.
        pending = max(-math.pi / 2, min(math.pi / 2, self._target - self.angle + turn))
        self._start, self._target = self.angle, self.angle + pending
        self._frame = 0
        if not ui.animated():
            self.angle, self._frame = self._target, ui.FRAMES - 1

    def step(self) -> None:
        self._frame += 1
        self.angle = self._start + (self._target - self._start) * ui.ease_out(self._frame / (ui.FRAMES - 1))


def _clip(s: str, width: int) -> str:
    return s if len(s) <= width else "…" + s[-(width - 1):]


def _clip_end(s: str, width: int) -> str:
    return s if len(s) <= width else s[:max(0, width - 1)] + "…"


class Picker:
    """A filterable list of Codex's models for the `model` field. A query that
    matches no model can still be used as typed, for models Codex doesn't list."""

    ROWS = 7

    def __init__(self, catalogue: list[Model], current: str | None) -> None:
        self.catalogue, self.current = catalogue, current
        self.query, self.index, self.top = "", 0, 0
        self.trail = ui.Trail()
        values = [value for value, _ in self.options()]
        if current in values:
            self._move(values.index(current))
            self.trail.clear()

    def options(self) -> list[tuple[str | None, Model | None]]:
        """(value, model) rows. The model is None for the Codex default and for
        a query used as typed."""
        found = models.search(self.catalogue, self.query)
        rows: list[tuple[str | None, Model | None]] = [(m.slug, m) for m in found]
        q = self.query.strip()
        if not q:
            rows.insert(0, (None, None))
        elif not any(m.slug == q for m in found):
            rows.append((q, None))
        return rows

    def _move(self, index: int) -> None:
        self.trail.move(self.index, index)
        self.index = index
        self.top = min(max(self.top, index - self.ROWS + 1), index)

    def handle(self, key: str) -> PickerResult:
        """Apply one keypress; returns ('pick', value), 'cancel' or None."""
        rows = self.options()
        if key == "esc":
            return "cancel"
        if key == "enter":
            return "pick", rows[self.index][0]
        if key in ("up", "down"):
            self._move((self.index + (-1 if key == "up" else 1)) % len(rows))
            return None
        if key == "\t":
            model = rows[self.index][1]
            if model:
                self.query = model.slug
        elif key == "backspace":
            self.query = self.query[:-1]
        elif len(key) == 1 and key.isprintable() and not key.isspace():
            self.query += key
        else:
            return None
        # Filtering reorders the rows, so an old trail would mark the wrong one.
        self.trail.clear()
        self.index = self.top = 0
        return None

    def lines(self, width: int) -> list[str]:
        rows = self.options()
        out = [ui.accent("? ") + ui.foam("model") + ui.accent(" › ") + self.query]
        slug_width = min(22, max([len(m.slug) for m in self.catalogue] + [13]))
        for i in range(self.top, min(len(rows), self.top + self.ROWS)):
            value, model = rows[i]
            selected = i == self.index
            if value is None:
                label, detail = "codex default", "let Codex choose"
            elif model is None:
                label, detail = value, "use as typed; not in Codex's model list"
            else:
                label, detail = value, model.description or model.name
            if value == self.current:
                detail += " · current"
            label = _clip(label, slug_width).ljust(slug_width)
            label = ui.foam(label) if selected else ui.accent(label) if model else ui.dim(label)
            out.append(f"  {self.trail.marker(i, selected)} {label}  {ui.dim(_clip_end(detail, width - slug_width - 6))}")
        if len(rows) > self.top + self.ROWS:
            out.append(ui.dim(f"    ↓ {len(rows) - self.top - self.ROWS} more"))
        if not self.catalogue:
            out.append(ui.dim("    no model list yet: run codex once and its models appear here"))
        return out


class Editor:
    """Edits one layer: a project's file, or the global file when `project` is None.
    Keys the layer doesn't set are inherited and shown dimmed with their source."""

    def __init__(self, project: Path | None = None) -> None:
        self.project = project
        self.base = config.inherited(project)
        data = config.read_file(project)
        self.values: config.Layer = {**self.base, **data}
        self.own = {f.key for f in FIELDS if f.key in data}
        self.saved, self.saved_own = dict(self.values), set(self.own)
        self.sources: config.Layer = {} if project is None else config.read_file(None)
        self.index = 0
        self.wheel = Wheel()
        self.status = ""
        self.quitting = False
        self.typing = False
        self.picker: Picker | None = None
        self.trail = ui.Trail()
        self.drawn = 0
        self.catalogue = models.load()
        overrides = [k for k in ("COMMENT_GATE", "COMMENT_GATE_MODE") if os.environ.get(k)]
        self.note = f"{' and '.join(overrides)} set in this shell; it overrides the file" if overrides else ""

    @property
    def field(self) -> Field:
        return FIELDS[self.index]

    def changed(self, key: str) -> bool:
        return self.values[key] != self.saved[key] or (key in self.own) != (key in self.saved_own)

    @property
    def dirty(self) -> bool:
        return any(self.changed(f.key) for f in FIELDS)

    def _set(self, key: str, value: Setting) -> None:
        self.values[key] = value
        self.own.add(key)

    def model(self) -> Model | None:
        """The catalogue entry for the configured model, if Codex lists it."""
        return next((m for m in self.catalogue if m.slug == self.values["model"]), None)

    def efforts(self) -> tuple[str, ...]:
        model = self.model()
        if model and model.efforts:
            return tuple(model.efforts)
        # When there are no effort options for the configured model, offer all
        # efforts supported by listed models.
        return tuple(dict.fromkeys(e for m in self.catalogue for e in m.efforts)) or EFFORTS

    def effort_unsupported(self) -> bool:
        return bool(self.catalogue) and self.values["reasoning_effort"] not in self.efforts()

    # --- drawing ------------------------------------------------------------

    def _value(self, f: Field, selected: bool) -> str:
        v, own = self.values[f.key], f.key in self.own
        if f.kind == "bool":
            shown = "on" if v else "off"
            shown = (ui.good if v else ui.caution)(shown) if own else ui.dim(shown)
        elif v in (None, ""):
            shown = ui.dim(f.unset)
        else:
            text = _clip(str(v), VALUE_WIDTH - 12)
            if f.key == "reasoning_effort" and self.effort_unsupported():
                shown = ui.caution(text)
            else:
                shown = (ui.foam(text) if selected else text) if own else ui.dim(text)
        if selected and f.kind != "text":
            shown = ui.dim("◂ ") + shown + ui.dim(" ▸")
        if not own and v not in (None, ""):
            shown += ui.dim(ui.italic(" · global" if f.key in self.sources else " · default"))
        return shown

    def _rows(self) -> list[str]:
        if self.project is None:
            title = ui.foam("global") + ui.dim(" · every project")
        else:
            title = ui.foam(_clip(ui.short(self.project), RIGHT_WIDTH - 12))
        if self.dirty:
            title += "  " + ui.caution("• unsaved")
        rows = [title, ui.dim("─" * (RIGHT_WIDTH - 2))]
        for i, f in enumerate(FIELDS):
            selected = i == self.index
            marker = self.trail.marker(i, selected)
            name =(ui.foam if selected else ui.accent)(f.key.ljust(NAME_WIDTH))
            changed = ui.caution(" •") if self.changed(f.key) else ""
            rows.append(f"{marker} {name} {self._value(f, selected)}{changed}")
        return rows

    def _help(self) -> str:
        f = self.field
        if f.key == "mode":
            return f"{f.help}: {config.MODE_HELP.get(str(self.values['mode']), 'unknown mode')}"
        model = self.model()
        if f.key == "model":
            if self.values["model"] in (None, ""):
                return f"{f.help}: unset lets Codex choose"
            if model:
                return f"{f.help}: {model.name}" + (f" · {model.description}" if model.description else "")
            if self.catalogue:
                return f"{f.help}: not in Codex's model list, so codex may reject it"
            return f"{f.help}: run codex once and its models can be picked here"
        if f.key == "reasoning_effort":
            effort = self.values["reasoning_effort"]
            if self.effort_unsupported():
                return f"{model.slug if model else 'no listed model'} supports no {effort} effort; " \
                       f"pick one of {', '.join(self.efforts())}"
            if model and isinstance(effort, str) and model.efforts.get(effort):
                return f"{f.help}: {model.efforts[effort]}"
        return f.help

    def _keys(self) -> str:
        """Return the active controls in descending order of importance."""
        back = ui.chip("esc") + " " + ui.foam("back")
        if self.typing:
            return back
        if self.picker:
            return "  ".join([ui.chip("↵") + " " + ui.foam("use"), ui.chip("esc") + " " + ui.foam("cancel"),
                              ui.accent("↑↓") + " " + ui.dim("choose"), ui.accent("tab") + " " + ui.dim("complete"),
                              ui.dim("type to filter")])
        f = self.field
        keys = [back]
        if f.kind != "text":
            keys.insert(0, ui.chip("←→") + " " + ui.foam("change"))
        keys.append(ui.accent("s") + " " + ui.dim("save"))
        if f.kind in ("int", "text"):
            keys.append(ui.accent("↵") + " " + ui.dim("type"))
        if f.kind == "model":
            keys.append(ui.accent("↵") + " " + ui.dim("pick"))
        if f.kind in ("text", "model"):
            keys.append(ui.accent("⌫") + " " + ui.dim("unset"))
        keys.append(ui.accent("r") + " " + ui.dim("inherit" if self.project else "default"))
        return "  ".join(keys)

    def lines(self) -> list[str]:
        rows = self._rows()
        if shutil.get_terminal_size().columns >= 2 + ui.WHEEL_WIDTH + 3 + RIGHT_WIDTH:
            wheel = ui.water_wheel(self.wheel.angle, running=bool(self.values["enabled"]))
            height = max(len(wheel), len(rows))
            # Centre the settings beside the wheel, above its waterline.
            top = max(0, (height - 2 - len(rows)) // 2)
            rows = [""] * top + rows + [""] * (height - top - len(rows))
            out = ["  " + (wheel[r] if r < len(wheel) else " " * ui.WHEEL_WIDTH) + "   " + rows[r]
                   for r in range(height)]
        else:
            out = ["  " + r for r in rows]
        if self.picker:
            width = shutil.get_terminal_size().columns - 3
            out += [""] + ["  " + l for l in self.picker.lines(width)]
        else:
            out += ["", "  " + ui.dim("↳ " + self._help())]
        out += ["", "  " + self._keys(), "  " + self.status]
        if self.note:
            out.append("  " + ui.caution("▲ ") + ui.dim(self.note))
        return out

    def draw(self, extra: int = 0) -> None:
        """Redraw in place; `extra` lines were printed below the last frame."""
        # Line wrapping would break cursor positioning on the next redraw.
        width = shutil.get_terminal_size().columns - 1
        out = [ui.fit(l, width) for l in self.lines()]
        if self.drawn or extra:
            sys.stdout.write(f"\x1b[{self.drawn + extra}A\r")
        sys.stdout.write("".join(l + "\x1b[K\n" for l in out) + "\x1b[J")
        sys.stdout.flush()
        self.drawn = len(out)

    # --- editing ------------------------------------------------------------

    def _change(self, direction: int) -> None:
        f, v = self.field, self.values[self.field.key]
        new: Setting
        choices: tuple[Setting, ...]
        if f.kind == "bool":
            new = not v
        elif f.kind == "choice":
            choices = self.efforts() if f.key == "reasoning_effort" else f.choices
            choices = choices if v in choices else (v,) + choices
            new = choices[(choices.index(v) + direction) % len(choices)]
        elif f.kind == "model":
            # A model Codex doesn't list stays reachable just after the default.
            slugs = tuple(m.slug for m in self.catalogue)
            choices = (None,) + (slugs if v in slugs or v is None else (v,) + slugs)
            new = choices[(choices.index(v) + direction) % len(choices)]
        elif f.kind == "int" and isinstance(v, (int, str)):
            new = max(f.low, int(v) + direction * f.step)
        else:
            return
        if new != v or f.key not in self.own:
            self._set(f.key, new)
            self.wheel.kick(-direction * CHANGE_TURN)

    def _where(self) -> str:
        return ui.short(config.file_path(self.project)) if self.project is None else ".claude/" + config.CONFIG_RELPATH.name

    def save(self) -> None:
        try:
            data = config.read_file(self.project)
            for f in FIELDS:
                if f.key in self.own:
                    data[f.key] = self.values[f.key]
                else:
                    data.pop(f.key, None)
            config.write_file(self.project, data)
        except (OSError, ValueError) as e:
            self.status = ui.bad("✖ ") + str(e)
            return
        self.saved, self.saved_own = dict(self.values), set(self.own)
        self.status = ui.good("✔ ") + "saved " + ui.dim(self._where())
        # The hook timeout is stored at install time. Refresh it in the
        # project's installed hooks, or in all known projects after a global
        # change.
        try:
            projects = installer.known_projects() if self.project is None else [self.project]
            updated = 0
            for project in projects:
                for name in installer.installed_in(project):
                    timeout = installer.hook_timeout(project, shared=name == "settings.json")
                    if timeout is not None and timeout != int(config.load(project)["timeout_sec"]) + 30:
                        installer.install(project, shared=name == "settings.json")
                        updated += 1
            if updated:
                self.status += ui.dim(f" · hook timeout updated in {updated} settings file(s)")
        except (OSError, ValueError) as e:
            self.status = ui.caution("▲ ") + f"saved, but the hook timeout was not updated: {e}"
        self.wheel.kick(SAVE_TURN)

    def handle(self, key: str) -> Literal["quit", "type"] | None:
        """Apply one keypress; returns 'quit', 'type' or None."""
        f = self.field
        quitting, self.quitting, self.status = self.quitting, False, ""
        if self.picker:
            result = self.picker.handle(key)
            if result == "cancel":
                self.picker = None
            elif isinstance(result, tuple):
                self.picker = None
                if result[1] != self.values[f.key] or f.key not in self.own:
                    self._set(f.key, result[1])
                    self.wheel.kick(-CHANGE_TURN)
            return None
        if key in ("up", "down", "k", "j"):
            step = -1 if key in ("up", "k") else 1
            to = (self.index + step) % len(FIELDS)
            self.trail.move(self.index, to)
            self.index = to
            self.wheel.kick(-step * MOVE_TURN)
        elif key in ("left", "right", "h", "l"):
            self._change(-1 if key in ("left", "h") else 1)
        elif key == "enter":
            if f.kind in ("int", "text"):
                return "type"
            if f.kind == "model":
                current = self.values[f.key]
                self.picker = Picker(self.catalogue, current if isinstance(current, str) else None)
                self.wheel.kick(MOVE_TURN)
                return None
            self._change(1)
        elif key == "backspace" and f.kind in ("text", "model") and (self.values[f.key] is not None or f.key not in self.own):
            self._set(f.key, None)
            self.wheel.kick(CHANGE_TURN)
        elif key in ("r", "R") and f.key in self.own:
            self.own.discard(f.key)
            self.values[f.key] = self.base[f.key]
            self.wheel.kick(2 * CHANGE_TURN)
        elif key in ("s", "S"):
            self.save()
        elif key in ("q", "Q", "esc"):
            if not self.dirty or quitting:
                return "quit"
            self.quitting = True
            self.status = ui.caution("▲ ") + "unsaved changes: " + ui.accent("s") + " to save, " \
                + ui.accent("esc") + " again to discard"
        return None

    def type_value(self) -> None:
        f = self.field
        current = self.values[f.key]
        self.typing = True
        self.draw()
        sys.stdout.write("\x1b[?25h")
        try:
            answer = ui.ask(f.key, "" if current is None else str(current)).strip()
        except ui.Back:
            return
        finally:
            self.typing = False
            sys.stdout.write("\x1b[?25l")
        new: Setting
        if f.kind == "int":
            try:
                new = int(answer)
            except ValueError:
                self.status = ui.bad("✖ ") + f"{f.key} must be a whole number"
                return
            if new < f.low:
                self.status = ui.bad("✖ ") + f"{f.key} must be at least {f.low}"
                return
        else:
            new = answer or None
        if new != current or f.key not in self.own:
            self._set(f.key, new)
            self.wheel.kick(-CHANGE_TURN)

    def _trails(self) -> list[ui.Trail]:
        return [self.trail] + ([self.picker.trail] if self.picker else [])

    def _animate(self, kb: ui.KeyReader) -> None:
        """Animate the wheel and fade the marker trails until both settle or a key
        arrives. Trails update faster than the wheel.
        """
        last_turn = time.monotonic()
        while self.wheel.spinning or any(t.fading for t in self._trails()):
            fading = any(t.fading for t in self._trails())
            if kb.ready(ui.Trail.FRAME_SEC if fading else ui.FRAME_SEC):
                return
            for t in self._trails():
                t.step()
            if self.wheel.spinning and time.monotonic() - last_turn >= ui.FRAME_SEC - ui.Trail.FRAME_SEC / 2:
                self.wheel.step()
                last_turn = time.monotonic()
            self.draw()

    def run(self) -> int:
        sys.stdout.write("\x1b[?25l")
        try:
            self.wheel.kick(OPEN_TURN)
            extra = 0
            while True:
                with ui.KeyReader() as kb:
                    self.draw(extra)
                    while True:
                        self._animate(kb)
                        action = self.handle(kb.read())
                        if action:
                            break
                        self.draw()
                if action == "quit":
                    return 0
                self.type_value()
                extra = 1
        finally:
            sys.stdout.write("\x1b[?25h")
            sys.stdout.flush()


def summary(editor: Editor) -> None:
    """Print the settings for terminals that can't run the editor."""
    rows = []
    for f in FIELDS:
        v = editor.values[f.key]
        shown = ("on" if v else "off") if f.kind == "bool" else f.unset if v in (None, "") else str(v)
        if f.key not in editor.own and v not in (None, ""):
            shown += ui.dim(" · global" if f.key in editor.sources else " · default")
        rows.append(f"{ui.accent(f.key.ljust(NAME_WIDTH))} {shown}")
    ui.box(rows, title=ui.short(config.file_path(editor.project)))


def edit(project: Path | None = None) -> int:
    """Edit a project's settings, or the global ones when `project` is None."""
    if project is not None:
        project = project.resolve()
        if not project.is_dir():
            ui.err(f"not a directory: {project}")
            return 1
    try:
        editor = Editor(project)
    except (OSError, ValueError) as e:
        ui.err(f"can't read {ui.short(config.file_path(project))}: {e}")
        return 1
    if not ui.interactive_input():
        summary(editor)
        ui.hint("run in an interactive terminal to edit, or edit the file directly")
        return 0
    return editor.run()
