"""Rollover: a prompt sent to a big project Session whose cache has gone cold is held back, the
Session's Handoff is written, and a fresh Session opens in its project with that prompt waiting.
Runs from roost's UserPromptSubmit hook (roost hook prompt), and only with "rollover": true in config."""
import json, os, subprocess, sys, time
from pathlib import Path

from roost.core import config
from roost.core import projectdata
from roost.core import sessiondata
from roost.core import summaries
from roost.core import terminals

SKIP = ">>"                 # a prompt starting with this goes to the cold Session anyway
DEFAULT_TOKENS = 100_000    # context at or above which a cold Session rolls over
LOCK_STALE = 15 * 60        # a lock older than this is from a rollover that died


def lock_path(sid):
    return config.ROOST_HOME / "rollover" / f"{sid}.lock"


def rolling(sid, now=None):
    """True while a rollover for sid is running."""
    try:
        return (now or time.time()) - lock_path(sid).stat().st_mtime < LOCK_STALE
    except OSError:
        return False


def project_listing(sid):
    """The project (or sub-project) whose sessions: lists sid, or None."""
    return next((p for p in projectdata.read_all() if sid in p["sessions"]), None)


def check(hook, now=None):
    """From a UserPromptSubmit hook payload: None to let the prompt through, or a block decision.
    Blocking also starts the rollover in the background, unless one is already running."""
    cfg = config.read_config()
    if cfg.get("rollover") is not True:
        return None
    sid, prompt = hook.get("session_id"), hook.get("prompt") or ""
    if not sid or prompt.lstrip().startswith(SKIP):
        return None
    p = project_listing(sid)
    if not p:
        return None
    pid = projectdata.project_id(p["path"])
    if rolling(sid, now):
        return {"decision": "block", "reason": f"This Session is already rolling over into a fresh {pid} Session. "
                                               f"Start a prompt with {SKIP} to send it here instead."}
    s = sessiondata.parse(Path(hook["transcript_path"]))
    left = sessiondata.cache_left(s, now)
    if left is None or left > 0 or s["tokens"] < cfg.get("rollover_tokens", DEFAULT_TOKENS):
        return None
    spawn(sid, prompt)
    return {"decision": "block",
            "reason": f"This Session's cache went cold ({s['tokens'] // 1000}k tokens to reload). Writing its Handoff "
                      f"and opening a fresh {pid} Session with this prompt waiting. Start a prompt with {SKIP} "
                      "to send it to this Session instead."}


def spawn(sid, prompt):
    """Start `roost rollover <sid> <prompt>` in the background, detached from the hook."""
    launcher = os.path.realpath(sys.argv[0]) if sys.argv and sys.argv[0] else "roost"
    subprocess.Popen([sys.executable, launcher, "rollover", sid, prompt], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def run(sid, prompt):
    """Write sid's Handoff, leave prompt waiting for its project, and start a fresh Session there.
    True when the fresh Session started."""
    p = project_listing(sid)
    if not p:
        config.log(f"rollover {sid[:8]}: no project lists it")
        return False
    pid = projectdata.project_id(p["path"])
    lock = lock_path(sid)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.touch()
    try:
        path = next(config.PROJECTS.glob(f"*/{sid}.jsonl"), None)
        s = sessiondata.parse(path) if path else {"name": sid[:8], "cwd": None, "home": None, "reply_at": None}
        covered = projectdata.read_project(Path(p["path"]))["covered"].get(sid)
        if not (covered and s["reply_at"] and covered >= s["reply_at"]):  # its last Handoff is older than its last reply
            try:
                summaries.summarize(sid)
            except ValueError as e:
                config.log(f"rollover {sid[:8]}: no Handoff written, so no fresh Session: {e}")
                return False
        projectdata.set_waiting(pid, prompt)
        term = terminals.chosen_terminal()
        started = terminals.start_session(pid, sessiondata.launch_dir(s), term, f"{s['name']} (cont.)") if term else None
        if not isinstance(started, str) or started.startswith("Couldn't"):
            projectdata.take_waiting(pid)
            config.log(f"rollover {sid[:8]}: Handoff written, but no fresh Session: {started or 'no terminal to open it in'}")
            return False
        config.log(f"rollover {sid[:8]}: {started}")
        return True
    finally:
        lock.unlink(missing_ok=True)


def hook_command(args):
    """roost hook prompt: Claude Code's UserPromptSubmit hook. Never blocks a prompt by failing."""
    try:
        decision = check(json.loads(sys.stdin.read()))
    except Exception as e:
        config.log(f"hook prompt: {e!r}")
        return 0
    if decision:
        print(json.dumps(decision))
    return 0


def rollover_command(args):
    return 0 if run(args.session, args.prompt) else 1
