import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "comment-gate.py"

EXISTING = {
    "permissions": {"allow": ["Bash(ls)"]},
    "hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "node other-tool.js"}]}],
        "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "node presence.js"}]}],
    },
}


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.project = base / "my project"
        self.settings = self.project / ".claude" / "settings.local.json"
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(json.dumps(EXISTING), encoding="utf-8")
        self.bin = base / "bin"
        self.env = {**os.environ, "COMMENT_GATE_HOME": str(base / "home"), "COMMENT_GATE_BIN": str(self.bin),
                    "NO_COLOR": "1"}
        self.env.pop("FORCE_COLOR", None)

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args, code=0):
        proc = subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True,
                              encoding="utf-8", env=self.env, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)
        return proc.stdout

    def load(self):
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def test_install_merges_and_is_idempotent(self):
        self.cli("install", str(self.project))
        self.cli("install", str(self.project))
        s = self.load()
        self.assertEqual(s["permissions"], EXISTING["permissions"])
        stop_cmds = [h["command"] for g in s["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(len(stop_cmds), 2)
        self.assertIn("node other-tool.js", stop_cmds)
        self.assertTrue(any(c.endswith("hook.py\" review") for c in stop_cmds))
        sub_cmds = [h["command"] for g in s["hooks"]["SubagentStop"] for h in g["hooks"]]
        self.assertEqual(len(sub_cmds), 1)
        self.assertTrue(sub_cmds[0].endswith("hook.py\" review"))
        pre = [g for g in s["hooks"]["PreToolUse"] if g["matcher"] == "Write|Edit|MultiEdit"]
        self.assertEqual(len(pre), 1)
        self.assertTrue((self.project / ".claude" / "comment-gate.json").is_file())

    def test_install_several_paths_with_mode_and_shared(self):
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        out = self.cli("install", str(self.project), str(other), "--mode", "report", "--shared")
        self.assertEqual(out.count("✔"), 2)
        for p in (self.project, other):
            self.assertTrue((p / ".claude" / "settings.json").is_file())
            cfg = json.loads((p / ".claude" / "comment-gate.json").read_text(encoding="utf-8"))
            self.assertEqual(cfg["mode"], "report")

    def test_install_bad_path_fails(self):
        out = self.cli("install", str(Path(self.tmp.name) / "missing"), code=1)
        self.assertIn("not a directory", out)

    def test_uninstall_removes_only_ours(self):
        self.cli("install", str(self.project))
        self.cli("uninstall", str(self.project))
        self.assertEqual(self.load(), EXISTING)

    def test_status_lists_known_projects(self):
        self.cli("install", str(self.project))
        out = self.cli("status")
        self.assertIn("my project", out)
        self.assertIn("apply", out)
        self.assertIn("settings.local.json", out)
        self.cli("uninstall", str(self.project))
        self.assertNotIn("my project", self.cli("status"))

    def test_register_and_unregister(self):
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env.get("PATH", "")
        out = self.cli("register")
        self.assertIn("already on PATH", out)
        shims = sorted(p.name for p in self.bin.iterdir())
        self.assertIn("comment-gate", shims)
        self.assertNotIn(b"\r", (self.bin / "comment-gate").read_bytes())
        if os.name == "nt":
            self.assertIn("comment-gate.cmd", shims)
            proc = subprocess.run([str(self.bin / "comment-gate.cmd"), "--version"], capture_output=True,
                                  text=True, env=self.env, timeout=60)
            self.assertIn("comment-gate", proc.stdout)
        self.cli("unregister")
        self.assertEqual(list(self.bin.iterdir()), [])

    def test_log_renders_changes(self):
        home = Path(self.env["COMMENT_GATE_HOME"])
        home.mkdir()
        rec = {"ts": "2026-11-18T10:00:00", "event": "reviewed", "mode": "apply", "comments": 2, "seconds": 3.1,
               "changes": [{"file": "a.js", "line": 3, "action": "rewrite", "old": "wordy", "new": "terse",
                            "reason": "padding"}]}
        (home / "log.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
        out = self.cli("log")
        self.assertIn("- wordy", out)
        self.assertIn("+ terse", out)

    def test_help_without_tty(self):
        out = self.cli()
        self.assertIn("╔═╗", out)
        self.assertIn("install", out)


if __name__ == "__main__":
    unittest.main()
