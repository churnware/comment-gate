import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXISTING = {
    "permissions": {"allow": ["Bash(ls)"]},
    "hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "node other-tool.js"}]}],
        "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "node presence.js"}]}],
    },
}


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        self.settings = self.project / ".claude" / "settings.local.json"
        self.settings.parent.mkdir()
        self.settings.write_text(json.dumps(EXISTING), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_install(self, *args):
        proc = subprocess.run([sys.executable, str(ROOT / "install.py"), str(self.project), *args],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def load(self):
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def test_install_merges_and_is_idempotent(self):
        self.run_install()
        self.run_install()
        s = self.load()
        self.assertEqual(s["permissions"], EXISTING["permissions"])
        stop_cmds = [h["command"] for g in s["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(len(stop_cmds), 2)
        self.assertIn("node other-tool.js", stop_cmds)
        self.assertTrue(any(c.endswith("hook.py\" review") for c in stop_cmds))
        pre = [g for g in s["hooks"]["PreToolUse"] if g["matcher"] == "Write|Edit|MultiEdit"]
        self.assertEqual(len(pre), 1)
        self.assertTrue((self.project / ".claude" / "comment-gate.json").is_file())

    def test_uninstall_removes_only_ours(self):
        self.run_install()
        self.run_install("--uninstall")
        self.assertEqual(self.load(), EXISTING)

    def test_status(self):
        self.run_install()
        out = self.run_install("--status")
        self.assertIn("installed in: settings.local.json", out)
        self.assertIn("codex:", out)


if __name__ == "__main__":
    unittest.main()
