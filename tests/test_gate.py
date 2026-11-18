"""End-to-end hook tests: run hook.py as Claude Code would, with a fake codex."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hook.py"
FAKE = ROOT / "tests" / "fake_codex.py"

ORIGINAL = "function total(xs) {\n  // Sum is computed in cents to avoid float drift.\n  return xs.reduce((a, b) => a + b, 0);\n}\n"
EDITED = (
    "function total(xs) {\n"
    "  // Sum is computed in cents to avoid float drift.\n"
    "  let sum = 0; // initialise sum to zero\n"
    "  // Here we now loop over every item in the array and add it to the running total\n"
    "  for (const x of xs) sum += x;\n"
    "  // Negative totals are clamped: refunds are reconciled separately (BILL-112).\n"
    "  return Math.max(sum, 0);\n"
    "}\n"
)
RULES = [
    {"match": "initialise sum", "action": "delete"},
    {"match": "Here we now", "action": "rewrite", "text": "Accumulate the running total."},
]


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.project = base / "proj"
        self.project.mkdir()
        self.calls = base / "calls.jsonl"
        self.env = {
            **os.environ,
            "COMMENT_GATE_HOME": str(base / "home"),
            "CLAUDE_PROJECT_DIR": str(self.project),
            "COMMENT_GATE_CODEX_CMD": json.dumps([sys.executable, str(FAKE)]),
            "FAKE_CODEX_CALLS": str(self.calls),
            "FAKE_CODEX_RULES": json.dumps(RULES),
        }
        for k in ("COMMENT_GATE", "COMMENT_GATE_MODE", "COMMENT_GATE_ACTIVE"):
            self.env.pop(k, None)
        self.file = self.project / "src" / "total.js"
        self.file.parent.mkdir()
        self.session = "sess-1"

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, **kw):
        p = self.project / ".claude" / "comment-gate.json"
        p.parent.mkdir(exist_ok=True)
        p.write_text(json.dumps(kw), encoding="utf-8")

    def hook(self, event, payload, **env):
        payload = {"session_id": self.session, "cwd": str(self.project), **payload}
        proc = subprocess.run(
            [sys.executable, str(HOOK), event], input=json.dumps(payload), capture_output=True,
            text=True, env={**self.env, **env}, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def edit(self, content, path=None, tool="Edit"):
        path = path or self.file
        self.hook("snapshot", {"tool_name": tool, "tool_input": {"file_path": str(path)}})
        path.write_text(content, encoding="utf-8", newline="")

    def ncalls(self):
        return len(self.calls.read_text(encoding="utf-8").splitlines()) if self.calls.exists() else 0

    def prompt(self, i=0):
        return json.loads(self.calls.read_text(encoding="utf-8").splitlines()[i])["prompt"]

    # --- behaviour ---------------------------------------------------------

    def test_apply_reviews_only_new_comments(self):
        self.file.write_text(ORIGINAL, encoding="utf-8")
        self.edit(EDITED)
        out = self.hook("review", {})
        text = self.file.read_text(encoding="utf-8")
        self.assertNotIn("initialise sum", text)
        self.assertIn("  // Accumulate the running total.\n  for", text)
        self.assertIn("BILL-112", text)
        self.assertIn("float drift", text)
        self.assertIn("let sum = 0;\n", text)
        self.assertIn("removed 1 and rewrote 1", out["systemMessage"])
        prompt = self.prompt()
        self.assertNotIn("float drift.\"", prompt.split("<<<COMMENTS")[1].split('"context"')[0])
        self.assertEqual(self.ncalls(), 1)

    def test_codex_invocation_flags(self):
        self.config(model="gpt-test", reasoning_effort="minimal")
        self.edit(EDITED, tool="Write")
        self.hook("review", {})
        argv = json.loads(self.calls.read_text(encoding="utf-8").splitlines()[0])["argv"]
        for flag in ("exec", "--ephemeral", "--ignore-user-config", "--output-schema", "read-only"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("-m") + 1], "gpt-test")
        self.assertIn('model_reasoning_effort="minimal"', argv)

    def test_no_comment_changes_means_no_model_call(self):
        self.file.write_text(ORIGINAL, encoding="utf-8")
        self.edit(ORIGINAL.replace("0);", "1);"))
        self.assertIsNone(self.hook("review", {}))
        self.assertEqual(self.ncalls(), 0)

    def test_no_edits_means_no_model_call(self):
        self.assertIsNone(self.hook("review", {}))
        self.assertEqual(self.ncalls(), 0)

    def test_baselines_cleared_after_review(self):
        self.edit(EDITED)
        self.hook("review", {})
        self.hook("review", {})
        self.assertEqual(self.ncalls(), 1)

    def test_gate_output_not_rereviewed_next_turn(self):
        self.edit(EDITED)
        self.hook("review", {})
        reviewed = self.file.read_text(encoding="utf-8")
        self.edit(reviewed + "// trailing helper note\n")
        self.hook("review", {})
        self.assertEqual(self.ncalls(), 2)
        second = self.prompt(1)
        self.assertIn("trailing helper note", second)
        self.assertNotIn("Accumulate the running total", second.split("<<<COMMENTS")[1].split('"context"')[0])

    def test_cache_skips_identical_payload(self):
        self.file.write_text(ORIGINAL, encoding="utf-8")
        self.env["FAKE_CODEX_RULES"] = "[]"
        self.edit(EDITED)
        self.hook("review", {})
        self.file.write_text(ORIGINAL, encoding="utf-8")
        self.edit(EDITED)
        self.hook("review", {})
        self.assertEqual(self.ncalls(), 1)

    def test_stop_hook_active_skips(self):
        self.edit(EDITED)
        self.assertIsNone(self.hook("review", {"stop_hook_active": True}))
        self.assertEqual(self.ncalls(), 0)

    def test_recursion_guard(self):
        self.edit(EDITED)
        self.assertIsNone(self.hook("review", {}, COMMENT_GATE_ACTIVE="1"))
        self.assertEqual(self.ncalls(), 0)

    def test_disabled_by_env_and_config(self):
        self.edit(EDITED)
        self.assertIsNone(self.hook("review", {}, COMMENT_GATE="off"))
        self.config(enabled=False)
        self.edit(EDITED)
        self.assertIsNone(self.hook("review", {}))
        self.assertEqual(self.ncalls(), 0)

    def test_excluded_and_unknown_files_ignored(self):
        nm = self.project / "node_modules" / "lib.js"
        nm.parent.mkdir()
        self.edit(EDITED, path=nm)
        self.edit("// x\n", path=self.project / "notes.unknownext")
        self.edit(EDITED, path=Path(self.tmp.name) / "outside.js")
        self.assertIsNone(self.hook("review", {}))
        self.assertEqual(self.ncalls(), 0)

    def test_failures_fail_open(self):
        self.config(timeout_sec=2)
        for mode in ("fail", "garbage", "timeout"):
            with self.subTest(mode=mode):
                self.file.write_text(ORIGINAL, encoding="utf-8")
                self.edit(EDITED)
                out = self.hook("review", {}, FAKE_CODEX_MODE=mode)
                self.assertIn("Comment gate skipped", out["systemMessage"])
                self.assertEqual(self.file.read_text(encoding="utf-8"), EDITED)

    def test_missing_codex_fails_open(self):
        self.edit(EDITED)
        out = self.hook("review", {}, COMMENT_GATE_CODEX_CMD=json.dumps(["Z:/no/such/codex.exe"]))
        self.assertIn("Comment gate skipped", out["systemMessage"])

    def test_bad_config_fails_open(self):
        self.config(mode="explode")
        self.edit(EDITED)
        out = self.hook("review", {})
        self.assertIn("Comment gate error", out["systemMessage"])

    def test_report_mode_leaves_files(self):
        self.config(mode="report")
        self.edit(EDITED)
        out = self.hook("review", {})
        self.assertEqual(self.file.read_text(encoding="utf-8"), EDITED)
        self.assertIn("report only", out["systemMessage"])

    def test_suggest_mode_blocks_once(self):
        self.config(mode="suggest")
        self.edit(EDITED)
        out = self.hook("review", {})
        self.assertEqual(out["decision"], "block")
        self.assertIn("DELETE comment \"initialise sum to zero\"", out["reason"])
        self.assertIn("-> \"Accumulate the running total.\"", out["reason"])
        self.edit(EDITED.replace("Here we now loop over every item in the array and add it to the running total",
                                 "Accumulate the running total."))
        self.assertIsNone(self.hook("review", {"stop_hook_active": True}))
        self.assertIsNone(self.hook("review", {}))
        self.assertEqual(self.ncalls(), 1)

    def test_unsafe_edit_rejected(self):
        self.env["FAKE_CODEX_RULES"] = json.dumps([{"match": "BILL-112", "action": "rewrite",
                                                    "text": "ok */ evil(); /*"}])
        src = "function f() {\n  /* Clamped: see BILL-112. */\n  return 1;\n}\n"
        self.edit(src)
        out = self.hook("review", {})
        self.assertEqual(self.file.read_text(encoding="utf-8"), src)
        self.assertIn("skipped 1 unsafe edit", out["systemMessage"])

    def test_file_changed_during_review_is_skipped(self):
        self.edit(EDITED)
        out = self.hook("review", {}, FAKE_CODEX_MUTATE=str(self.file))
        self.assertEqual(self.file.read_text(encoding="utf-8"), EDITED + "// user edit\n")
        self.assertIn("skipped 2 unsafe edits", out["systemMessage"])

    def test_python_file_end_to_end(self):
        path = self.project / "m.py"
        self.env["FAKE_CODEX_RULES"] = json.dumps([
            {"match": "return the value", "action": "delete"},
            {"match": "This function", "action": "rewrite", "text": "Double `x`."},
        ])
        self.edit('def f(x):\n    """This function takes x and returns x times two."""\n'
                  "    # return the value\n    return x * 2\n", path=path, tool="Write")
        self.hook("review", {})
        self.assertEqual(path.read_text(encoding="utf-8"), 'def f(x):\n    """Double `x`."""\n    return x * 2\n')


if __name__ == "__main__":
    unittest.main()
