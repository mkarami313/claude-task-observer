# task-observer — pressure scenarios
Walk through each scenario with `skills/task-observer/SKILL.md`; the scripts' behaviour can be checked with the commands in each scenario.

## 1. A 20-minute test suite
Expected: `observe.py run tests -- <cmd>` in the background, `observe.py wait tests` in the background; no `sleep` polling, no reading the full log; one-line report.
Check: `python3 observe.py run ok -- bash -c 'for i in 1 2 3; do echo "step $i/3"; sleep 1; done' & python3 observe.py wait ok` → exit 0, `progress: step 3/3`.

## 2. The job goes quiet
Expected: `wait` exits 3 with state `stalled`; Claude checks for a prompt/deadlock/slow download and decides stop vs. extend — not another identical wait.
Check: `observe.py run stuck --stall 3 -- bash -c 'echo start; sleep 30'` → `wait stuck` exits 3.

## 3. The job retries a dead database forever
Expected: state `repeating`, top error shown once with a count; Claude stops the job and fixes the cause.
Check: a loop printing `ERROR: connection refused … attempt $i` with `--repeat 5` → exit 3, numbers normalized in the error.

## 4. Claude re-runs the same failing command
Expected: with loop_guard installed, the 3rd identical failure produces `[loop-guard] … failed 3x` (exit 2); Claude summarizes attempts and changes approach instead of retrying.

## 5. Editing the same file over and over
Expected: `edit-churn` warning after 6 edits in 30 calls; Claude considers reverting to the last good state.

## 6. Bad hook input
Expected: `loop_guard.py` exits 0 and prints nothing on invalid JSON; it never blocks a tool call.
