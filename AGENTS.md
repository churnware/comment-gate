# claude-comment-gate

An automatic comment-quality gate for Claude Code. When Claude finishes a turn, an independent OpenAI model (via the Codex CLI and your existing Codex login) reviews only the comments Claude **added or modified**. It removes redundant, obvious, conversational and misleading comments, tightens verbose ones, and keeps rationale, constraints, invariants, caveats, API docs and tool directives.

## How it works

| Hook | What it does | Cost |
|---|---|---|
| `PreToolUse` (`Write\|Edit\|MultiEdit`) | Snapshots a file before Claude's first edit to it in the session | ~50 ms, no model call |
| `Stop` | Diffs each snapshot of the main agent's edits against the current file, extracts new or changed comments, and sends them to `codex exec`. It then applies the returned edits | One model call per 40 comments, run in parallel, and only if comments changed |
| `SubagentStop` | Does the same for the edits of the subagent that just finished, so the main agent's `Stop` never reviews files a background subagent is still editing | Same as `Stop` |

Large reviews are split into batches of up to `max_comments` comments and `max_chars` characters, and the batches are reviewed in parallel. When there are more than `max_batches` batches, whole files wait for the next review. A single file too large for every batch combined is reviewed up to the limits. The notice reports any deferred or unreviewed comments.

Safety:
- **Comments only.** After each edit the gate compares a comment-free token skeleton of the file with the original. Any edit that would change code is discarded, and for Python the file must still parse. Docstrings may be rewritten but not deleted.
- **Directives are never sent for review.** This covers `eslint-*`, `@ts-*`, `noqa`, `type: ignore`, `//go:`, `#region`, `prettier-ignore`, license/SPDX headers, shebangs and similar comments. You can add your own patterns with `protected_patterns`.
- **No loops.**
  - Snapshots are cleared after every review, so the gate's own output is never re-reviewed.
  - `stop_hook_active` is honoured.
  - The Codex child runs with `COMMENT_GATE_ACTIVE=1`.
  - Identical payloads are cached per session.
- **Fails open.** If Codex is missing, times out, is not authenticated or returns bad output, the gate logs the problem, shows a one-line notice and lets Claude stop normally.
- **Concurrent edits.** If a file changes while the review is running, the gate leaves that file alone.
- **Formatting.** Rewritten comments are rewrapped to the width the original was wrapped at, or to 80 columns for a comment that was a single line. Typographic quotes from the reviewer are straightened unless the original comment used them.

Codex runs with `--ephemeral --ignore-user-config -s read-only` in an empty temp directory, so your MCP servers and plugins don't load and the reviewer can't touch the project.

## Install

Requirements: Python 3.10+ and the Codex CLI logged in (`codex login`). The gate looks for Codex in this order: `codex_path` in the config, then `codex` on PATH, then the binary bundled with the OpenAI VS Code extension.

```sh
python comment-gate.py register   # puts `comment-gate` on PATH (shims in ~/.local/bin)
comment-gate                      # interactive menu (esc goes back)
```

| Command | What it does |
|---|---|
| `comment-gate install [PATH...]` | Adds the hooks to one or more projects (default: the current directory). `--mode apply\|suggest\|report` sets the mode. `--shared` writes `.claude/settings.json` instead of the personal `settings.local.json` |
| `comment-gate uninstall [PATH...]` | Removes the hooks and leaves your other hooks alone |
| `comment-gate config [PATH]` | Opens the settings editor for a project (default: the current directory). `--global` edits the defaults every project inherits |
| `comment-gate status [PATH...]` | Shows install state and mode for every project you've installed into, plus the Codex and PATH status |
| `comment-gate test [--live]` | Checks Python, the Codex binary, auth, PATH and the state directory. `--live` also runs a real review call |
| `comment-gate log [-n N]` | Shows recent reviews as coloured diffs, each with its reason |
| `comment-gate register` / `unregister` | Adds or removes the PATH shims. On Windows, `register` also adds `~/.local/bin` to your user PATH if it's missing |

Paths can be typed, pasted or dragged into the terminal. Quote any path that contains spaces, and use `~` and environment variables freely. The installer merges into your existing settings and creates `.claude/comment-gate.json` if that file doesn't exist. The new file holds only the mode you chose, so every other setting follows the global config. Restart the Claude Code session (or open `/hooks`) to pick the hooks up. Projects installed before the `SubagentStop` hook existed keep working, with `Stop` reviewing every edit. Run `install` again to add the hook. Colour is disabled automatically when output isn't a terminal or `NO_COLOR` is set.

## Configuration

Settings are layered. The built-in defaults come first, then the global `~/.claude/comment-gate/config.json`, then `<project>/.claude/comment-gate.json`, then the environment overrides. A key a file doesn't set comes from the layer before it.

`comment-gate config` edits either file in the terminal. Use ↑↓ to pick a setting, ←→ to change it and ↵ to type a value. `r` makes a setting inherit again, `s` saves and esc goes back. Inherited values are dimmed and labelled `global` or `default`. For `model`, ←→ steps through the models Codex lists for your account and ↵ opens a picker: type to filter, tab completes, and a name Codex doesn't list can still be used as typed. `reasoning_effort` offers only the efforts the chosen model supports, and the help line shows Codex's description of each. Saving a new `timeout_sec` also updates the hook timeout in every project the change reaches. The lists (`include`, `exclude`, `protected_patterns`) are edited in the JSON files directly.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Master switch |
| `mode` | `"apply"` | `apply`: edit the files in place. `suggest`: block Stop once and hand the edits to Claude. `report`: only print what would change |
| `model` | `null` | Codex model (`null` = Codex default) |
| `reasoning_effort` | `"low"` | Passed as `model_reasoning_effort` |
| `timeout_sec` | `90` | Review timeout. The Stop hook timeout is set to this value + 30 at install time |
| `max_comments` / `max_chars` | `40` / `24000` | Caps on each Codex call's payload |
| `max_batches` | `6` | Most Codex calls per review. Files beyond this wait for the next review |
| `context_lines` | `6` | Lines of surrounding code sent with each comment |
| `include` / `exclude` | `**` / vendor, build and generated files | Globs relative to the project |
| `protected_patterns` | `[]` | Extra regexes. A comment that matches one is never reviewed |
| `codex_path` | `null` | Explicit path to the Codex binary |
| `notify` | `true` | Show a one-line summary after each review |

Environment overrides:
- `COMMENT_GATE=off` disables the gate.
- `COMMENT_GATE_MODE=report|suggest|apply` sets the mode.
- `COMMENT_GATE_HOME` relocates the state and log directory (default `~/.claude/comment-gate`).

Every review is logged to `~/.claude/comment-gate/log.jsonl`, including each change and its reason.

## Notes and limitations

- Only edits made through Claude's `Write`, `Edit` and `MultiEdit` tools are tracked. Changes made through Bash (`sed`, scripts) are not.
- Only files inside the project (`CLAUDE_PROJECT_DIR`) are reviewed. Edits to supported files elsewhere are listed in the notice, then ignored.
- In `apply` mode the files change after Claude's turn ends. If Claude edits the same file next turn, it re-reads the file first.
- Supported languages:
  - Python
  - C-family: JS/TS, Java, C#, C/C++, Go, Rust, Swift, Kotlin, PHP, CSS/SCSS
  - Hash-comment languages: shell, PowerShell, Ruby, YAML, TOML
  - SQL, Lua, Haskell
  - HTML/XML/Vue/Svelte, including JS/TS in `<script>` and CSS/SCSS/Less in `<style>`

  Other file types are ignored.

## Tests

```sh
python -m unittest discover -s tests -t .   # offline; uses tests/fake_codex.py
python tests/live_smoke.py                  # real Codex call on a sample file
python -m mypy                              # strict type check (pip install mypy); settings in mypy.ini
```

The code is fully annotated and passes `mypy --strict`. Every module starts with `from __future__ import annotations`, so a class can name itself in its own annotations. Code for one platform goes under `sys.platform` checks, which mypy understands, rather than `os.name`.
