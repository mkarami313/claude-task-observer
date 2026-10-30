#!/usr/bin/env python3
"""loop_guard.py — Claude Code PostToolUse hook that notices when the agent is going in circles.

Reads the hook JSON from stdin, appends one record per tool call to
<project>/.observer/agent-<session>.jsonl, and, when a pattern is found, prints a short
warning to stderr and exits 2 so Claude sees it. Otherwise exits 0 silently.

Patterns (in the last WINDOW calls):
  same-call   the same tool with the same input >= SAME times
  same-fail   the same failing call (normalized) >= FAIL times
  fail-streak FAIL_STREAK failed calls in a row
  edit-churn  the same file edited >= EDITS times
Each pattern warns at most once per COOLDOWN calls.

Tune with env vars: LOOP_GUARD_WINDOW (30) _SAME (3) _FAIL (3) _FAIL_STREAK (5) _EDITS (6)
_COOLDOWN (10). Disable with LOOP_GUARD_OFF=1. Python 3 standard library only.
"""
import hashlib
import json
import os
import re
import sys
import time


def env_int(name, default):
    try:
        return int(os.environ.get(f"LOOP_GUARD_{name}", default))
    except ValueError:
        return default


WINDOW = env_int("WINDOW", 30)
SAME = env_int("SAME", 3)
FAIL = env_int("FAIL", 3)
FAIL_STREAK = env_int("FAIL_STREAK", 5)
EDITS = env_int("EDITS", 6)
COOLDOWN = env_int("COOLDOWN", 10)
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
NUM = re.compile(r"\d+")


def is_failure(resp):
    if resp is None:
        return False
    if isinstance(resp, str):
        return resp.lstrip().lower().startswith(("error", "<tool_use_error>"))
    if isinstance(resp, dict):
        if resp.get("is_error") or resp.get("error") or resp.get("interrupted"):
            return True
        for k in ("exit_code", "exitCode", "returncode", "returnCode"):
            if isinstance(resp.get(k), int) and resp[k] != 0:
                return True
        if resp.get("success") is False:
            return True
    if isinstance(resp, list):
        return any(isinstance(b, dict) and b.get("is_error") for b in resp)
    return False


def key_of(tool, tool_input, normalize=False):
    try:
        s = json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(tool_input)
    if normalize:
        s = NUM.sub("#", s)
    return hashlib.sha1(f"{tool}|{s}".encode("utf-8", "replace")).hexdigest()[:16]


def describe(tool, tool_input):
    if not isinstance(tool_input, dict):
        return tool
    for k in ("command", "file_path", "pattern", "url", "query", "description", "prompt"):
        if k in tool_input:
            return f"{tool}: {str(tool_input[k]).splitlines()[0][:100] if str(tool_input[k]) else ''}"
    return tool


def main():
    if os.environ.get("LOOP_GUARD_OFF") == "1":
        return 0
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0                                  # never break the session on bad input
    tool = data.get("tool_name", "?")
    tin = data.get("tool_input", {})
    failed = is_failure(data.get("tool_response"))
    session = re.sub(r"[^\w-]", "_", str(data.get("session_id", "default")))[:64]
    root = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    d = os.path.join(root, ".observer")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return 0
    path = os.path.join(d, f"agent-{session}.jsonl")

    rec = {"t": round(time.time(), 1), "tool": tool, "k": key_of(tool, tin),
           "nk": key_of(tool, tin, normalize=True), "fail": failed, "desc": describe(tool, tin)}
    if tool in EDIT_TOOLS and isinstance(tin, dict):
        rec["file"] = tin.get("file_path") or tin.get("notebook_path")

    hist = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh.readlines()[-(WINDOW + COOLDOWN + 50):]:
                try:
                    hist.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    calls = [h for h in hist if "k" in h] + [rec]
    recent = calls[-WINDOW:]

    def cooled(pattern):
        n_since = 0
        for h in reversed(hist):
            if h.get("warn") == pattern:
                return n_since >= COOLDOWN
            if "k" in h:
                n_since += 1
        return True

    msgs = []
    same = sum(1 for h in recent if h["k"] == rec["k"])
    if same >= SAME and cooled("same-call"):
        msgs.append(("same-call", f"You have run the identical call {same}x in the last {len(recent)} tool calls: {rec['desc']}. "
                                  "Unless something it depends on has changed since, the result will be the same: use the result you already have, or change the approach."))
    if failed:
        same_fail = sum(1 for h in recent if h["nk"] == rec["nk"] and h["fail"])
        if same_fail >= FAIL and cooled("same-fail"):
            msgs.append(("same-fail", f"This call has failed {same_fail}x: {rec['desc']}. Stop retrying it. Read the error, "
                                      "form one hypothesis about the cause, and test that instead."))
        streak = 0
        for h in reversed(recent):
            if not h["fail"]:
                break
            streak += 1
        if streak >= FAIL_STREAK and cooled("fail-streak"):
            msgs.append(("fail-streak", f"{streak} tool calls in a row have failed. Pause: summarize what you tried and why "
                                        "each failed, then pick a different approach or ask the user."))
    if rec.get("file"):
        edits = sum(1 for h in recent if h.get("file") == rec["file"])
        if edits >= EDITS and cooled("edit-churn"):
            msgs.append(("edit-churn", f"{rec['file']} was edited {edits}x in the last {len(recent)} calls. If you are "
                                       "fixing your own fixes, revert to the last good state and rethink the change."))

    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            for pattern, _ in msgs:
                fh.write(json.dumps({"t": rec["t"], "warn": pattern}) + "\n")
    except OSError:
        pass

    if msgs:
        print("[loop-guard] " + " ".join(m for _, m in msgs), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
