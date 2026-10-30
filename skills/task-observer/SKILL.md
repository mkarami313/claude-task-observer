---
name: task-observer
description: Watch long-running tasks and report on them without burning context — builds, test suites, migrations, data jobs, training runs, deploys, servers — and catch an agent going in circles. Use when a command may take more than a minute or two, when running several jobs in parallel, when asked to monitor, babysit, keep an eye on or report on a task, or to set up the loop-guard hook that warns on repeated or failing tool calls.
---

# Task observer

Two failure modes on long work: **polling blind** (re-reading huge logs, `sleep` loops, or forgetting the task exists) and **going in circles** (re-running the same failing command, re-editing the same file). This skill fixes both with two stdlib-only scripts in `<skill-dir>/scripts/`.

## Long-running commands — `observe.py`
Copy once per project (so commands stay short): `mkdir -p .observer && cp <skill-dir>/scripts/observe.py .observer/` and add `.observer/` to `.git/info/exclude`.

1. **Start** every long command through the observer, in the background (Bash `run_in_background: true`):
   `python3 .observer/observe.py run <name> [--stall 300] [--repeat 20] -- <command ...>`
   It logs all output to `.observer/<name>.log` and keeps `.observer/<name>.json` current: state, elapsed, idle time, last progress line (`37%`, `120/500`, `epoch 4`), and the most repeated error line (numbers/paths normalized).
2. **Wait** with the observer, also in the background, so you are re-invoked when something happens instead of polling:
   `python3 .observer/observe.py wait <name> --timeout 1800`
   Exit codes: `0` done · `1` failed/stopped/lost · `3` **stalled** (no output for `--stall` s) or **repeating** (same error `--repeat` times) · `4` timeout, still running.
3. **Check** any time: `observe.py status` (one line per task) or `observe.py status <name> --tail 20`.
4. **Stop**: `observe.py stop <name>` (SIGTERM to the whole process group).

Rules:
- **Never read the full log into the conversation.** Use `status <name>`; if you need more, `Grep` the log for the error, then Read 40–80 lines around it.
- On **stalled**: check whether it is waiting for input (prompts, `y/N`, auth), a deadlock, or genuinely slow (a big download with no progress output). Decide stop vs. extend; don't just wait again with the same timeout.
- On **repeating**: the job is retrying something that won't fix itself (DB down, wrong URL, missing file). Stop it, fix the cause, restart.
- Choose `--stall` from the job: ~120 s for builds/tests, 600+ s for jobs that print rarely (DB migrations, model training without per-step logs).
- Parallel jobs: one name each, one `wait` each; report with one `status` call.
- Report to the user in **one line per task** (name · state · elapsed · progress · top error), not log excerpts, unless they ask.

## Agent loops — `loop_guard.py` (PostToolUse hook)
Install when the user wants it, or when a long autonomous run is starting. Add to `.claude/settings.json` (project) or `~/.claude/settings.json` (user), merging with existing hooks:
```json
{ "hooks": { "PostToolUse": [ { "matcher": "*", "hooks": [
  { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.observer/loop_guard.py\"" } ] } ] } }
```
and copy `<skill-dir>/scripts/loop_guard.py` to `.observer/`. It records each tool call (a hash, not the content) in `.observer/agent-<session>.jsonl` and warns Claude (stderr + exit 2) when, within the last 30 calls:
| Pattern | Default trigger |
|---|---|
| same-call | identical tool + input ≥3× |
| same-fail | the same failing call (numbers ignored) ≥3× |
| fail-streak | 5 failed calls in a row |
| edit-churn | one file edited ≥6× |
Each warning fires at most once per 10 calls. Tune with `LOOP_GUARD_*` env vars (see the script header); `LOOP_GUARD_OFF=1` disables it. It never blocks a call and exits 0 on any internal error.

**When you see a `[loop-guard]` message**, do what it says before the next tool call: state in one or two sentences what you tried and why it failed, then change approach (different hypothesis, smaller repro, read the actual error) or ask the user. Do not re-run the same call to "check again".

## Subagents and parallel agents
- Give each background agent a task name and ask it to finish with one status line (`name · done/failed · result · evidence`).
- A long-running agent's own shell work should go through `observe.py` too, so the parent can `status` it without reading its transcript.

## When NOT to use
Commands that finish in under a minute (just run them), interactive commands (they will stall — run them yourself or ask the user), and services meant to run forever (start with `observe.py run` but don't `wait`; check `status` when needed).
