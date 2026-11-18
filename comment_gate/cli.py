"""`comment-gate` command line interface.

Run with no arguments in a terminal for the interactive menu."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import config, installer, pathreg, reviewer, ui

VERSION = "0.2.0"
MODES = ("apply", "suggest", "report")
MODE_HELP = {
    "apply": "edit comments in place after each turn",
    "suggest": "hand the edits back to Claude to apply",
    "report": "only show what would change",
}


def _resolve(paths):
    """Resolve CLI path arguments, defaulting to the current directory."""
    out = []
    for p in paths or ["."]:
        p = Path(os.path.expandvars(os.path.expanduser(str(p)))).resolve()
        if not p.is_dir():
            ui.err(f"not a directory: {p}")
            continue
        out.append(p)
    return out


def _root_hint(project):
    root = installer.git_root(project)
    if root and root != project:
        ui.hint(f"inside the git repo {ui.short(root)}; Claude Code reads hooks from the folder it is opened in")


# --- commands ---------------------------------------------------------------

def cmd_install(paths, shared=False, mode=None, title=True):
    if title:
        ui.heading("Install")
    projects = _resolve(paths)
    for project in projects:
        try:
            settings, cfg_path, created = installer.install(project, shared, mode)
            cfg = config.load(project)
        except (OSError, ValueError) as e:
            ui.err(f"{ui.bold(ui.short(project))}: {e}")
            continue
        ui.ok(f"{ui.bold(ui.short(project))} {ui.dim('→')} .claude/{settings.name} "
              f"{ui.dim('·')} mode {ui.accent(cfg['mode'])}")
        if created:
            ui.hint(f"created .claude/{cfg_path.name} with defaults")
        _root_hint(project)
    if projects:
        print()
        ui.info(f"restart Claude Code sessions in {'this project' if len(projects) == 1 else 'these projects'} "
                f"(or open {ui.accent('/hooks')}) to activate")
    return 0 if projects else 1


def cmd_uninstall(paths, title=True):
    if title:
        ui.heading("Uninstall")
    projects = _resolve(paths)
    for project in projects:
        changed = installer.uninstall(project)
        if changed:
            ui.ok(f"{ui.bold(ui.short(project))} {ui.dim('·')} removed from "
                  + ", ".join(f".claude/{p.name}" for p in changed))
        else:
            ui.info(f"{ui.bold(ui.short(project))} {ui.dim('·')} was not installed")
    return 0 if projects else 1


def _project_rows(projects):
    rows = []
    for p in projects:
        where = installer.installed_in(p)
        try:
            cfg = config.load(p)
            mode = cfg["mode"] if cfg["enabled"] else "disabled"
        except (OSError, ValueError) as e:
            mode = f"bad config ({e})"
        rows.append((p, where, mode))
    return rows


def cmd_status(paths):
    ui.heading("Status")
    projects = _resolve(paths) if paths else installer.known_projects()
    if not projects:
        ui.info("no projects yet: run " + ui.accent("comment-gate install <path>"))
    width = max([len(ui.short(p)) for p in projects] + [7])
    for p, where, mode in _project_rows(projects):
        name = ui.short(p).ljust(width)
        if where:
            colour = ui.caution if mode in ("disabled", "report", "suggest") else ui.good
            ui.ok(f"{ui.bold(name)}  {colour(mode.ljust(8))} {ui.dim(', '.join(where))}")
        else:
            ui.err(f"{ui.bold(name)}  {ui.dim('not installed')}")
    print()
    try:
        codex = " ".join(reviewer.find_codex({"codex_path": None}))
        ui.info(f"codex   {ui.dim(ui.short(codex))}")
    except reviewer.ReviewError:
        ui.warn("codex   not found")
    ui.info(f"command {ui.dim('on PATH' if pathreg.registered() else 'not registered (comment-gate register)')}")
    ui.info(f"log     {ui.dim(ui.short(config.state_root() / 'log.jsonl'))}")
    return 0


def _check(label, fn):
    try:
        good, detail = fn()
    except Exception as e:  # a doctor check must never crash the report
        good, detail = False, str(e)
    (ui.ok if good is True else ui.warn if good is None else ui.err)(f"{label.ljust(12)} {ui.dim(detail)}")
    return good is not False


def cmd_doctor(live=False, project=None, title=True):
    if title:
        ui.heading("Doctor")
    results = []

    def python():
        v = sys.version_info
        return v >= (3, 9), f"{v.major}.{v.minor}.{v.micro} {ui.short(sys.executable)}"

    codex = []

    def find():
        cfg = config.load(project) if project else dict(config.DEFAULTS)
        codex.extend(reviewer.find_codex(cfg))
        proc = subprocess.run(codex + ["--version"], capture_output=True, text=True, timeout=20)
        return proc.returncode == 0, f"{proc.stdout.strip() or proc.stderr.strip()} · {ui.short(codex[-1])}"

    def auth():
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        if (home / "auth.json").is_file():
            return True, f"credentials in {ui.short(home)}"
        if os.environ.get("OPENAI_API_KEY"):
            return True, "OPENAI_API_KEY set"
        return False, "not logged in: run `codex login`"

    def command():
        if not pathreg.registered():
            return None, "not registered: run `comment-gate register`"
        return True, f"shims in {ui.short(pathreg.bin_dir())}" + ("" if pathreg.on_path(pathreg.bin_dir())
                                                                 else " (open a new terminal)")

    def state_dir():
        root = config.state_root()
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, f"writable {ui.short(root)}"

    results.append(_check("python", python))
    results.append(_check("codex", find))
    results.append(_check("auth", auth))
    results.append(_check("command", command))
    results.append(_check("state", state_dir))

    if live:
        results.append(_live_check(project))
    elif all(results):
        ui.hint("add --live for a real end-to-end review call")
    print()
    if all(results):
        ui.ok(ui.good("all good"))
        return 0
    ui.err(ui.bad("some checks failed"))
    return 1


def _live_check(project):
    from . import comments

    sample = "let total = 0; // set total to zero\n// Here we now add up every price in the cart\nfor (const p of prices) total += p;\n"
    units = comments.units(sample, "js")
    items = [reviewer.build_item(f"c{i + 1}", "sample.js", "js", u, sample, 3) for i, u in enumerate(units)]
    cfg = config.load(project) if project else dict(config.DEFAULTS)
    t0 = time.monotonic()
    try:
        with ui.Spinner("asking codex to review 2 sample comments"):
            decisions = reviewer.review(items, cfg)
    except reviewer.ReviewError as e:
        ui.err(f"{'live review'.ljust(12)} {ui.dim(str(e))}")
        return False
    ui.ok(f"{'live review'.ljust(12)} {ui.dim(f'{time.monotonic() - t0:.1f}s · {len(decisions)} change(s)')}")
    for cid, d in decisions.items():
        old = next(i["comment"] for i in items if i["id"] == cid)
        ui.hint(f"{d['action']}: {old!r}" + (f" → {d['text']!r}" if d["text"] else ""))
    return True


def cmd_log(count=5):
    ui.heading("Recent reviews")
    log = config.state_root() / "log.jsonl"
    if not log.is_file():
        ui.info("nothing logged yet")
        return 0
    records = []
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("event") in ("reviewed", "suggested", "review_failed", "error"):
            records.append(rec)
    if not records:
        ui.info("nothing logged yet")
    for rec in records[-count:]:
        print()
        when = ui.dim(rec["ts"].replace("T", " "))
        if rec["event"] in ("review_failed", "error"):
            ui.err(f"{when}  {ui.bad(rec.get('error', 'error'))}")
            continue
        summary = f"{rec.get('comments', 0)} reviewed · {len(rec.get('changes', []))} changed · {rec.get('seconds', '?')}s"
        ui.ok(f"{when}  {ui.accent(rec.get('mode', 'suggest'))}  {ui.dim(summary)}")
        for c in rec.get("changes", []):
            print(f"    {ui.dim(c['file'] + ':' + str(c['line']))}  {ui.dim(ui.italic(c['reason']))}")
            for l in c["old"].splitlines():
                print(f"      {ui.bad('- ' + l)}")
            for l in c["new"].splitlines():
                print(f"      {ui.good('+ ' + l)}")
    return 0


def cmd_register():
    ui.heading("Register")
    shims, status = pathreg.register()
    for s in shims:
        ui.ok(f"wrote {ui.short(s)}")
    if status == "present":
        ui.ok(f"{ui.short(pathreg.bin_dir())} is already on PATH")
    elif status == "added":
        ui.ok(f"added {ui.short(pathreg.bin_dir())} to your user PATH")
        ui.hint("open a new terminal to pick it up")
    else:
        ui.warn(f"{ui.short(pathreg.bin_dir())} is not on PATH; add this to your shell profile:")
        ui.hint('export PATH="$HOME/.local/bin:$PATH"')
    print()
    ui.info(f"try it: {ui.accent('comment-gate status')}")
    return 0


def cmd_unregister():
    ui.heading("Unregister")
    removed = pathreg.unregister()
    for s in removed:
        ui.ok(f"removed {ui.short(s)}")
    if not removed:
        ui.info("no shims were installed")
    return 0


# --- interactive ------------------------------------------------------------

def _pick_projects(question):
    known = installer.known_projects()
    if not known:
        return ui.ask_paths(question)
    print(f"  {ui.accent('?')} {ui.foam(question)}")
    for i, p in enumerate(known, 1):
        print(f"    {ui.wave(f'[{i}]')} {ui.short(p)}")
    print(f"    {ui.dim('or type / drag in a path')}")
    while True:
        answer = ui.ask("Projects", "1")
        picks = answer.replace(",", " ").split()
        if picks and all(p.isdigit() and 1 <= int(p) <= len(known) for p in picks):
            return [known[int(p) - 1] for p in picks]
        paths = ui.split_paths(answer)
        if paths and all(p.is_dir() for p in paths):
            return [p.resolve() for p in paths]
        ui.err("enter numbers from the list or existing folder paths")


def _page(title=None, first=False):
    """Clear the terminal and draw a fresh page so output never piles up."""
    ui.clear()
    ui.banner(VERSION, animate=first)
    if title:
        ui.heading(title)
        print()


def interactive():
    first = True
    while True:
        menu = [
            ("1", "Install into project(s)"),
            ("2", "Uninstall from project(s)"),
            ("3", "Status"),
            ("4", "Doctor"),
            ("5", "Recent reviews"),
            ("6", "Re-register on PATH" if pathreg.registered() else "Register `comment-gate` on PATH"),
            ("q", "Quit"),
        ]
        _page(first=first)
        first = False
        choice = ui.choose("What would you like to do?", menu, "1")
        if choice == "q":
            ui.clear()
            return 0
        if choice == "1":
            _page("Install")
            paths = ui.ask_paths("Project folder(s) to protect", str(Path.cwd()))
            mode = ui.choose("Mode", [(m[0], f"{m.ljust(8)} {ui.dim(MODE_HELP[m])}") for m in MODES], "a")
            shared = ui.confirm("Commit hooks to the shared .claude/settings.json?", False)
            print()
            cmd_install(paths, shared, next(m for m in MODES if m[0] == mode), title=False)
        elif choice == "2":
            _page("Uninstall")
            projects = _pick_projects("Which project(s)?")
            print()
            cmd_uninstall(projects, title=False)
        elif choice == "3":
            _page()
            cmd_status([])
        elif choice == "4":
            _page("Doctor")
            live = ui.confirm("Include a live review against Codex?", True)
            print()
            cmd_doctor(live=live, title=False)
        elif choice == "5":
            _page()
            cmd_log()
        elif choice == "6":
            _page()
            cmd_register()
        ui.pause()


# --- entry ------------------------------------------------------------------

EPILOG = """examples:
  comment-gate                         interactive menu
  comment-gate install .               protect the current project
  comment-gate install ~/a "D:/My B"   several projects at once
  comment-gate install . --mode report try it without editing files
  comment-gate status                  every project you've installed into
  comment-gate doctor --live           verify codex end to end
  comment-gate log -n 10               recent reviews as diffs
"""


def build_parser():
    ap = argparse.ArgumentParser(prog="comment-gate", description="Codex-reviewed comment quality gate for Claude Code.",
                                 epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-V", "--version", action="version", version=f"comment-gate {VERSION}")
    sub = ap.add_subparsers(dest="cmd", metavar="command")

    p = sub.add_parser("install", help="add the hooks to project(s)")
    p.add_argument("paths", nargs="*", type=Path, help="project folders (default: .)")
    p.add_argument("--shared", action="store_true", help="write .claude/settings.json instead of settings.local.json")
    p.add_argument("--mode", choices=MODES, help="set the review mode in .claude/comment-gate.json")

    p = sub.add_parser("uninstall", help="remove the hooks from project(s)")
    p.add_argument("paths", nargs="*", type=Path)

    p = sub.add_parser("status", help="show projects, codex and PATH status")
    p.add_argument("paths", nargs="*", type=Path, help="default: all known projects")

    p = sub.add_parser("doctor", help="check that everything needed is in place")
    p.add_argument("--live", action="store_true", help="also make a real review call")
    p.add_argument("--project", type=Path, help="use this project's config for the codex call")

    p = sub.add_parser("log", help="show recent reviews")
    p.add_argument("-n", type=int, default=5, help="number of reviews (default 5)")

    sub.add_parser("register", help="put `comment-gate` on PATH")
    sub.add_parser("unregister", help="remove the `comment-gate` shims")
    sub.add_parser("menu", help="interactive menu")
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    try:
        if a.cmd is None:
            if ui.TTY and sys.stdin.isatty():
                return interactive()
            ui.banner(VERSION)
            ap.print_help()
            return 0
        if a.cmd == "menu":
            return interactive()
        if a.cmd in ("install", "uninstall", "status") and ui.TTY:
            ui.banner(VERSION)
        if a.cmd == "install":
            return cmd_install(a.paths, a.shared, a.mode)
        if a.cmd == "uninstall":
            return cmd_uninstall(a.paths)
        if a.cmd == "status":
            return cmd_status(a.paths)
        if a.cmd == "doctor":
            return cmd_doctor(a.live, a.project.resolve() if a.project else None)
        if a.cmd == "log":
            return cmd_log(a.n)
        if a.cmd == "register":
            return cmd_register()
        if a.cmd == "unregister":
            return cmd_unregister()
    except KeyboardInterrupt:
        print()
        return 130
    return 0
