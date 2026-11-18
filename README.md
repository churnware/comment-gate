# claude-comment-gate

An automatic comment-quality gate for Claude Code. When Claude finishes a turn, an independent OpenAI model (via the Codex CLI and your existing Codex login) reviews only the comments Claude **added or modified**. It removes redundant, obvious, conversational and misleading comments, tightens verbose ones, and keeps rationale, constraints, invariants, caveats, API docs and tool directives.

## How it works

| Hook | What it does | Cost |
|---|---|---|
| `PreToolUse` (`Write\|Edit\|MultiEdit`) | Snapshots a file before Claude's first edit to it in the session | ~50 ms, no model call |
| `Stop` | Diffs each snapshot against the current file, extracts new or changed comments, and sends them all in **one** `codex exec` call. It then applies the returned edits | One model call, and only if comments changed |

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

Codex runs with `--ephemeral --ignore-user-config -s read-only` in an empty temp directory, so your MCP servers and plugins don't load and the reviewer can't touch the project.

## Install

Requirements: Python 3.9+ and the Codex CLI logged in (`codex login`). The gate looks for Codex in this order: `codex_path` in the config, then `codex` on PATH, then the binary bundled with the OpenAI VS Code extension.

```sh
python comment-gate.py register   # puts `comment-gate` on PATH (shims in ~/.local/bin)
comment-gate                      # interactive menu
```

| Command | What it does |
|---|---|
| `comment-gate install [PATH...]` | Adds the hooks to one or more projects (default: the current directory). `--mode apply\|suggest\|report` sets the mode. `--shared` writes `.claude/settings.json` instead of the personal `settings.local.json` |
| `comment-gate uninstall [PATH...]` | Removes the hooks and leaves your other hooks alone |
| `comment-gate status [PATH...]` | Shows install state and mode for every project you've installed into, plus the Codex and PATH status |
| `comment-gate doctor [--live]` | Checks Python, the Codex binary, auth, PATH and the state directory. `--live` also runs a real review call |
| `comment-gate log [-n N]` | Shows recent reviews as coloured diffs, each with its reason |
| `comment-gate register` / `unregister` | Adds or removes the PATH shims. On Windows, `register` also adds `~/.local/bin` to your user PATH if it's missing |

Paths can be typed, pasted or dragged into the terminal. Quote any path that contains spaces, and use `~` and environment variables freely. The installer merges into your existing settings and creates `.claude/comment-gate.json` if that file doesn't exist. Restart the Claude Code session (or open `/hooks`) to pick the hooks up. Colour is disabled automatically when output isn't a terminal or `NO_COLOR` is set.

## Configuration: `<project>/.claude/comment-gate.json`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Master switch |
| `mode` | `"apply"` | `apply`: edit the files in place. `suggest`: block Stop once and hand the edits to Claude. `report`: only print what would change |
| `model` | `null` | Codex model (`null` = Codex default) |
| `reasoning_effort` | `"low"` | Passed as `model_reasoning_effort` |
| `timeout_sec` | `90` | Review timeout. The Stop hook timeout is set to this value + 30 at install time |
| `max_comments` / `max_chars` | `40` / `24000` | Caps on the payload size |
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
- In `apply` mode the files change after Claude's turn ends. If Claude edits the same file next turn, it re-reads the file first.
- Supported languages:
  - Python
  - C-family: JS/TS, Java, C#, C/C++, Go, Rust, Swift, Kotlin, PHP, CSS/SCSS
  - Hash-comment languages: shell, PowerShell, Ruby, YAML, TOML
  - SQL, Lua, Haskell
  - HTML/XML/Vue/Svelte

  Other file types are ignored.

## Tests

```sh
python -m unittest discover -s tests -t .   # offline; uses tests/fake_codex.py
python tests/live_smoke.py                  # real Codex call on a sample file
```
