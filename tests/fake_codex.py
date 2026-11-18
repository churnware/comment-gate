"""Stand-in for `codex exec` driven by environment variables.

FAKE_CODEX_MODE   ok (default) | timeout | fail | garbage
FAKE_CODEX_RULES  JSON list of {"match", "action", "text"}; applied to every
                  comment whose body contains `match`
FAKE_CODEX_CALLS  file to which each received prompt is appended
FAKE_CODEX_MUTATE file to append a line to, simulating a concurrent edit
"""

import json
import os
import sys
import time

BEGIN, END = "<<<COMMENTS", "COMMENTS>>>"


def main():
    args = sys.argv[1:]
    prompt = sys.stdin.read()
    calls = os.environ.get("FAKE_CODEX_CALLS")
    if calls:
        with open(calls, "a", encoding="utf-8") as f:
            f.write(json.dumps({"argv": args, "prompt": prompt}) + "\n")

    mutate = os.environ.get("FAKE_CODEX_MUTATE")
    if mutate:
        with open(mutate, "a", encoding="utf-8", newline="") as f:
            f.write("// user edit\n")

    mode = os.environ.get("FAKE_CODEX_MODE", "ok")
    if mode == "timeout":
        time.sleep(30)
    if mode == "fail":
        print("error: not logged in", file=sys.stderr)
        return 1

    out = args[args.index("-o") + 1]
    if mode == "garbage":
        payload = "I think these comments look fine!"
    else:
        items = json.loads(prompt[prompt.index(BEGIN) + len(BEGIN) : prompt.index(END)])
        rules = json.loads(os.environ.get("FAKE_CODEX_RULES", "[]"))
        decisions = []
        for item in items:
            for r in rules:
                if r["match"] in item["comment"]:
                    decisions.append({"id": item["id"], "action": r["action"], "text": r.get("text", ""),
                                      "reason": "test"})
                    break
        payload = json.dumps({"decisions": decisions})
    with open(out, "w", encoding="utf-8") as f:
        f.write(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
