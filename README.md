# task-observer

Watch long-running tasks without burning context, and catch Claude Code going in circles.

A [Claude skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview) for Claude Code — Python 3 standard library only, no dependencies. Part of the [mkarami313/Claude-skill](https://github.com/mkarami313/Claude-skill) collection.

Long-running work fails in two ways: Claude **polls blind** (re-reads huge logs, sleeps in loops, or forgets the job exists) or **goes in circles** (re-runs the same failing command, re-edits the same file). `task-observer` fixes both with two small, dependency-free scripts.

**Use it when** a command may take more than a minute or two (builds, test suites, migrations, data jobs, training, deploys), when several jobs run in parallel, when you ask Claude to "keep an eye on" something — or to install the loop guard for long autonomous runs.

## How it works
| Piece | What it does |
|---|---|
| `observe.py run <name> -- <cmd>` | Runs the command in the background, logs to `.observer/<name>.log`, keeps `.observer/<name>.json` current: state, elapsed, idle time, last progress line (`37%`, `120/500`, `epoch 4`), most-repeated error (numbers and paths normalized). |
| `observe.py wait <name>` | Blocks until something happens and prints one status block. Exit `0` done · `1` failed · `3` **stalled** (no output for `--stall` s) or **repeating** (same error `--repeat` times) · `4` timeout. Run it in the background and Claude is woken up only when it matters. |
| `observe.py status [name]` | One line per task, or details for one. |
| `loop_guard.py` (PostToolUse hook) | Warns Claude when, in the last 30 tool calls, the same call repeats 3×, the same call fails 3×, 5 calls fail in a row, or one file is edited 6×. Warnings go to Claude (exit 2) at most once per 10 calls; it never blocks a call. |

## Loop guard setup
Copy `skills/task-observer/scripts/loop_guard.py` to `.observer/` in your project and add to `.claude/settings.json`:
```json
{ "hooks": { "PostToolUse": [ { "matcher": "*", "hooks": [
  { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.observer/loop_guard.py\"" } ] } ] } }
```
Tune with `LOOP_GUARD_WINDOW`, `_SAME`, `_FAIL`, `_FAIL_STREAK`, `_EDITS`, `_COOLDOWN`; disable with `LOOP_GUARD_OFF=1`.

## What's inside
```
skills/task-observer/
├── SKILL.md                 when to observe, how to react to stalled/repeating/failed, loop-guard rules
└── scripts/
    ├── observe.py           run / wait / status / stop for long commands
    └── loop_guard.py        PostToolUse hook that detects agent loops
```

## Installation

### Claude Code — plugin marketplace (recommended)
```
/plugin marketplace add mkarami313/claude-task-observer
/plugin install task-observer@claude-task-observer
```
Update later with `/plugin marketplace update claude-task-observer`.

### Claude Code — copy the skill folder
```bash
git clone https://github.com/mkarami313/claude-task-observer
cp -r claude-task-observer/skills/task-observer <your-repo>/.claude/skills/     # this project only
cp -r claude-task-observer/skills/task-observer ~/.claude/skills/               # all your projects
```

### Claude.ai / Claude desktop app
Zip the folder so that `task-observer/SKILL.md` is at the root of the archive and upload it in Claude's skills settings:
```bash
cd claude-task-observer/skills && zip -r task-observer.zip task-observer
```

## Testing
`tests/task-observer/scenarios.md` lists pressure scenarios. After changing the skill, walk through each one with the new `SKILL.md` (or give it to a fresh Claude session) and check the expected behaviour still follows.

## Contributing
Issues and PRs welcome. Keep `SKILL.md` lean (long material → `references/`, code → `scripts/`), scripts stdlib-only with LF endings, and add a scenario for every behaviour you change.

## License
[MIT](LICENSE)
