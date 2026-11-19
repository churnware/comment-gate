<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="media/logo-dark.webp">
    <img src="media/logo-light.webp" alt="Comment Gate" width="531">
  </picture>
</p>

<p align="center"><em>A second opinion on Claude's code comments.</em></p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-4e76a0?style=flat-square&labelColor=2e4a70" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/claude_code-washed-4e76a0?style=flat-square&labelColor=2e4a70" alt="Claude Code: washed">
  <img src="https://img.shields.io/badge/reviewer-another_fucking_robot-4e76a0?style=flat-square&labelColor=2e4a70" alt="Reviewer: another fucking robot">
</p>

<p align="center">
  <img src="media/showcase.png" alt="The comment-gate menu beside a review log of rewritten comments" width="100%">
</p>

<p align="center"><img src="media/wave.svg" alt="" width="100%"></p>

## How It Works

When Claude finishes a turn, comment-gate sends *only* the comments it added or changed to an OpenAI model through your Codex login. Redundant, obvious and chatty comments are removed or tightened. Rationale, caveats, API docs and tool directives stay.

Comment Gate ensures your code is never touched. Any edits that touch more than a comment get trashed!

## Install

You need Python 3.10+ and the [Codex CLI](https://github.com/openai/codex), logged in with `codex login`.

```sh
python comment-gate.py register   # adds `comment-gate` to your PATH
comment-gate install              # adds the hooks to the current project
```

Restart your Claude Code session afterwards. Run `comment-gate` on its own for an interactive menu covering install, status, a health check and recent reviews.

## Configure

`comment-gate config` opens a settings editor for the current project, and `--global` sets the defaults every project inherits.

| | |
|---|---|
| **Mode** | `apply` edits files in place, `suggest` hands the edits back to Claude, `report` only shows what would change |
| **Model** | The reviewer model and its reasoning effort |
| **Limits** | Timeout and batch sizes for large reviews |
| **Scope** | Which files are reviewed, and which comments are always left alone |

Set the environment variable `COMMENT_GATE=off` to turn it off. The full reference is in [AGENTS.md](AGENTS.md).

<p align="center"><img src="media/wave.svg" alt="" width="100%"></p>
