#!/usr/bin/env python3
"""observe.py — watch long-running commands without reading their output.

  observe.py run  <name> [--stall SEC] [--repeat N] [--dir D] -- <command ...>
      Runs the command, logs output to <dir>/<name>.log and keeps <dir>/<name>.json
      up to date (state, elapsed, idle, progress, repeating errors). Exits with the
      command's exit code. Start it in the background.
  observe.py wait <name> [--timeout SEC] [--dir D]
      Blocks until the task is done, failed, stalled or repeating (or the timeout);
      prints one status block. Exit: 0 done, 1 failed, 3 stalled/repeating,
      4 timeout (still running), 2 usage/unknown task.
  observe.py status [<name>] [--tail N] [--dir D]
      One line per task, or details (last N lines, top errors) for one task.
  observe.py stop <name> [--dir D]
      Sends SIGTERM to the task's process group.

Default dir: .observer   States: running, stalled, repeating, done, failed, stopped, lost.
Python 3 standard library only.
"""
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections import Counter, deque

DEFAULT_DIR = ".observer"
ERR_RE = re.compile(r"\b(error|exception|fatal|failed|failure|traceback|panic|denied|timed? ?out|refused)\b", re.I)
PROGRESS_RE = re.compile(r"(\d{1,3}(?:\.\d+)?\s?%|\b\d+\s?/\s?\d+\b|\b(?:epoch|step|batch|chunk|page)\s*[:=]?\s*\d+)", re.I)
NORM_RES = [(re.compile(r"0x[0-9a-f]+", re.I), "0x#"),
            (re.compile(r"\b[0-9a-f]{8,}\b", re.I), "#hex"),
            (re.compile(r"\d+(\.\d+)?"), "#"),
            (re.compile(r"(/[\w.@-]+)+"), "/path"),
            (re.compile(r"\s+"), " ")]


def norm(line):
    s = line.strip()
    for rx, rep in NORM_RES:
        s = rx.sub(rep, s)
    return s[:160]


def fmt_dur(sec):
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m{sec % 60:02d}s"
    return f"{sec // 3600}h{(sec % 3600) // 60:02d}m"


def paths(d, name):
    return os.path.join(d, f"{name}.json"), os.path.join(d, f"{name}.log")


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def parse_opts(argv, spec):
    """spec: {flag: default}; returns (opts, positional, rest_after_dashdash)."""
    opts = dict(spec)
    pos, rest = [], []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            rest = argv[i + 1:]
            break
        if a.startswith("--") and a[2:] in spec:
            if i + 1 >= len(argv):
                raise SystemExit(f"{a} needs a value")
            opts[a[2:]] = type(spec[a[2:]])(argv[i + 1]) if spec[a[2:]] is not None else argv[i + 1]
            i += 2
            continue
        pos.append(a)
        i += 1
    return opts, pos, rest


# ---------------------------------------------------------------- run
def cmd_run(argv):
    opts, pos, command = parse_opts(argv, {"stall": 300, "repeat": 20, "dir": DEFAULT_DIR})
    if len(pos) != 1 or not command:
        print("usage: observe.py run <name> [--stall SEC] [--repeat N] -- <command ...>", file=sys.stderr)
        return 2
    name = pos[0]
    os.makedirs(opts["dir"], exist_ok=True)
    jpath, lpath = paths(opts["dir"], name)

    log = open(lpath, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    st = {"name": name, "cmd": " ".join(command), "pid": proc.pid, "observer_pid": os.getpid(),
          "started": time.time(), "state": "running", "exit": None, "lines": 0,
          "last_output": time.time(), "progress": None, "last_error": None, "top_errors": [],
          "stall_after": opts["stall"], "repeat_limit": opts["repeat"], "log": lpath}
    lock = threading.Lock()
    errors = Counter()
    tail = deque(maxlen=50)

    def reader():
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            log.write(line + "\n")
            log.flush()
            with lock:
                st["lines"] += 1
                st["last_output"] = time.time()
                tail.append(line)
                for part in line.split("\r"):
                    m = PROGRESS_RE.search(part)
                    if m:
                        st["progress"] = part.strip()[:120]
                if ERR_RE.search(line):
                    st["last_error"] = line.strip()[:200]
                    errors[norm(line)] += 1

    def forward(signum, _frame):
        try:
            os.killpg(proc.pid, signum)
        except ProcessLookupError:
            pass

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)
    t = threading.Thread(target=reader, daemon=True)
    t.start()

    while True:
        code = proc.poll()
        with lock:
            now = time.time()
            st["elapsed"] = round(now - st["started"], 1)
            st["idle"] = round(now - st["last_output"], 1)
            st["top_errors"] = [[e, n] for e, n in errors.most_common(3)]
            if code is None:
                if errors and errors.most_common(1)[0][1] >= opts["repeat"]:
                    st["state"] = "repeating"
                elif st["idle"] >= opts["stall"]:
                    st["state"] = "stalled"
                else:
                    st["state"] = "running"
            st["updated"] = now
            write_json(jpath, st)
        if code is not None:
            break
        time.sleep(2)

    t.join(timeout=5)
    log.close()
    with lock:
        st["exit"] = code
        st["state"] = "done" if code == 0 else ("stopped" if code in (-15, 143) else "failed")
        st["ended"] = time.time()
        st["elapsed"] = round(st["ended"] - st["started"], 1)
        st["top_errors"] = [[e, n] for e, n in errors.most_common(3)]
        write_json(jpath, st)
    print(summary(st, list(tail)[-5:]))
    return code if code >= 0 else 128 - code


# ---------------------------------------------------------------- helpers for readers
def load(d, name):
    jpath, _ = paths(d, name)
    try:
        with open(jpath, encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        return None
    if st.get("state") in ("running", "stalled", "repeating"):
        now = time.time()
        if not pid_alive(st.get("observer_pid", st.get("pid", 0))):
            st["state"] = "lost"          # observer died without writing a final state
        else:
            st["idle"] = round(now - st.get("last_output", now), 1)
            st["elapsed"] = round(now - st.get("started", now), 1)
    return st


def line_for(st):
    bits = [f"{st['name']:<16}", f"{st['state']:<9}", f"elapsed={fmt_dur(st.get('elapsed', 0))}",
            f"lines={st.get('lines', 0)}"]
    if st["state"] in ("running", "stalled", "repeating"):
        bits.append(f"idle={fmt_dur(st.get('idle', 0))}")
    if st.get("exit") is not None:
        bits.append(f"exit={st['exit']}")
    if st.get("progress"):
        bits.append(f"progress=\"{st['progress'][:60]}\"")
    if st.get("top_errors"):
        e, n = st["top_errors"][0]
        bits.append(f"top_error=({n}x) {e[:70]}")
    return "  ".join(bits)


def tail_lines(path, n):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 16384))
            data = fh.read().decode("utf-8", errors="replace").splitlines()
        return data[-n:]
    except OSError:
        return []


def summary(st, tail):
    out = [f"task={st['name']} state={st['state']} exit={st.get('exit')} elapsed={fmt_dur(st.get('elapsed', 0))} "
           f"lines={st.get('lines', 0)} log={st.get('log')}"]
    if st.get("progress"):
        out.append(f"progress: {st['progress']}")
    for e, n in st.get("top_errors", []):
        out.append(f"error x{n}: {e}")
    if tail:
        out.append("last lines:")
        out += [f"  {l[:200]}" for l in tail]
    return "\n".join(out)


# ---------------------------------------------------------------- wait / status / stop
def cmd_wait(argv):
    opts, pos, _ = parse_opts(argv, {"timeout": 1800, "dir": DEFAULT_DIR})
    if len(pos) != 1:
        print("usage: observe.py wait <name> [--timeout SEC]", file=sys.stderr)
        return 2
    name, deadline = pos[0], time.time() + opts["timeout"]
    st = None
    for _ in range(15):                      # the run may not have written its file yet
        st = load(opts["dir"], name)
        if st:
            break
        time.sleep(1)
    if not st:
        print(f"unknown task: {name} (no {opts['dir']}/{name}.json)")
        return 2
    while st["state"] == "running" and time.time() < deadline:
        time.sleep(5)
        st = load(opts["dir"], name) or st
    print(summary(st, tail_lines(st.get("log", ""), 8)))
    return {"done": 0, "failed": 1, "stopped": 1, "lost": 1, "stalled": 3, "repeating": 3}.get(st["state"], 4)


def cmd_status(argv):
    opts, pos, _ = parse_opts(argv, {"tail": 10, "dir": DEFAULT_DIR})
    if pos:
        st = load(opts["dir"], pos[0])
        if not st:
            print(f"unknown task: {pos[0]}")
            return 2
        print(summary(st, tail_lines(st.get("log", ""), opts["tail"])))
        return 0
    try:
        names = sorted(f[:-5] for f in os.listdir(opts["dir"]) if f.endswith(".json"))
    except OSError:
        names = []
    if not names:
        print("no tasks")
        return 0
    for n in names:
        st = load(opts["dir"], n)
        if st:
            print(line_for(st))
    return 0


def cmd_stop(argv):
    opts, pos, _ = parse_opts(argv, {"dir": DEFAULT_DIR})
    if len(pos) != 1:
        print("usage: observe.py stop <name>", file=sys.stderr)
        return 2
    st = load(opts["dir"], pos[0])
    if not st or st["state"] not in ("running", "stalled", "repeating"):
        print(f"{pos[0]}: not running")
        return 1
    try:
        os.killpg(st["pid"], signal.SIGTERM)
    except ProcessLookupError:
        pass
    print(f"{pos[0]}: SIGTERM sent to process group {st['pid']}")
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 2
    cmds = {"run": cmd_run, "wait": cmd_wait, "status": cmd_status, "stop": cmd_stop}
    if argv[0] not in cmds:
        print(f"unknown command {argv[0]}\n\n{__doc__.strip()}", file=sys.stderr)
        return 2
    return cmds[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
