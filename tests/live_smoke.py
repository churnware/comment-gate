"""Real end-to-end run against the installed codex (network + ChatGPT auth).

    python tests/live_smoke.py [--model MODEL]

Simulates Claude writing a new file full of mixed-quality comments, runs the
Stop hook, prints the result and verifies that only comments changed."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comment_gate import apply  # noqa: E402

SAMPLE = '''\
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Json = any;

/**
 * Retry an async operation with exponential backoff.
 *
 * @param fn operation to retry; must be idempotent because it may run more than once
 * @param attempts maximum number of tries, including the first
 * @returns the first successful result
 */
export async function retry<T>(fn: () => Promise<T>, attempts = 3): Promise<T> {
  // Here we initialise the variable that will hold the last error we saw
  let lastError: unknown;
  // loop over attempts
  for (let i = 0; i < attempts; i++) {
    try {
      return await fn(); // call the function
    } catch (err) {
      lastError = err;
      // Cap the delay at 10s: the upstream gateway drops idle connections after 15s.
      const delay = Math.min(2 ** i * 100, 10_000);
      // So basically what we are doing here is we are going to wait for the delay amount of
      // milliseconds before trying again, which gives the server some time to recover.
      await new Promise((r) => setTimeout(r, delay));
    }
  }
  // I added this throw as you requested so that the caller gets the error
  throw lastError;
}

// TODO(billing): remove once the v2 client handles retries itself.
export const DEFAULT_ATTEMPTS = 3; // default number of attempts
'''


def hook(event, payload, env):
    proc = subprocess.run([sys.executable, str(ROOT / "hook.py"), event], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=300)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--effort", default="low")
    a = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "proj"
        (project / ".claude").mkdir(parents=True)
        cfg = {"reasoning_effort": a.effort, "timeout_sec": 240}
        if a.model:
            cfg["model"] = a.model
        (project / ".claude" / "comment-gate.json").write_text(json.dumps(cfg), encoding="utf-8")
        env = {**os.environ, "COMMENT_GATE_HOME": str(Path(tmp) / "home"), "CLAUDE_PROJECT_DIR": str(project)}
        for k in ("COMMENT_GATE_CODEX_CMD", "COMMENT_GATE", "COMMENT_GATE_MODE", "COMMENT_GATE_ACTIVE"):
            env.pop(k, None)

        target = project / "retry.ts"
        base = {"session_id": "live", "cwd": str(project)}
        hook("snapshot", {**base, "tool_name": "Write", "tool_input": {"file_path": str(target)}}, env)
        target.write_text(SAMPLE, encoding="utf-8")

        t0 = time.monotonic()
        out = hook("review", base, env)
        elapsed = time.monotonic() - t0
        result = target.read_text(encoding="utf-8")

        print("hook output:", json.dumps(out, indent=2))
        print(f"wall time: {elapsed:.1f}s\n")
        print(result)
        log = Path(tmp) / "home" / "log.jsonl"
        if log.exists():
            for line in log.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                for c in rec.get("changes", []):
                    print(f"  L{c['line']} {c['action']}: {c['old']!r} -> {c['new']!r}  ({c['reason']})")
                if rec["event"] != "reviewed":
                    print("  log:", rec)

        assert apply.skeleton(result, "js") == apply.skeleton(SAMPLE, "js"), "code changed!"
        for must in ("eslint-disable", "@param fn", "must be idempotent", "15s", "TODO(billing)"):
            assert must in result, f"lost valuable content: {must}"
        print("\nOK: code unchanged and valuable comments preserved")


if __name__ == "__main__":
    main()
