"""Checkpoints: a project Session that keeps working gets a Handoff about once an hour, so a crash, a lost
transcript, or a forgotten /wrap-up costs at most that hour. Runs from roost's Stop hook (roost hook stop),
in the background, and only with "checkpoint": true in config. It writes the Handoff alone: the README
waits for /wrap-up, so it isn't rewritten under the person while they work."""
import json, os, subprocess, sys, time
from pathlib import Path

from roost.core import config
from roost.core import projectdata
from roost.core import rollover
from roost.core import sessiondata
from roost.core import summaries

DEFAULT_MINUTES = 60  # config checkpoint_minutes: how much unsaved work a Session may build up


def attempt_path(sid):
    """Touched as each checkpoint starts, so a failing one is retried an interval later, not on every reply."""
    return config.ROOST_HOME / "checkpoint" / f"{sid}.last"


def last_attempt(sid):
    try:
        return attempt_path(sid).stat().st_mtime
    except OSError:
        return None


def due(sid, transcript, now=None, cfg=None):
    """True when sid should get a Handoff now: checkpoints are on, a project lists it, it has replied since
    its newest Handoff (or has none), that Handoff (or its start) is an interval old, and no checkpoint
    or rollover for it started within the interval."""
    cfg = config.read_config() if cfg is None else cfg
    if cfg.get("checkpoint") is not True or not sid:
        return False
    now = time.time() if now is None else now
    every = 60 * float(cfg.get("checkpoint_minutes") or DEFAULT_MINUTES)
    tried = last_attempt(sid)
    if (tried and now - tried < every) or rollover.rolling(sid, now):
        return False
    p = rollover.project_listing(sid)
    if not p or not transcript or not Path(transcript).is_file():
        return False
    s = sessiondata.parse(Path(transcript))
    covered = projectdata.read_project(Path(p["path"]))["covered"].get(sid)
    since = covered or s["started"]
    return bool(s["reply_at"] and since and s["reply_at"] > (covered or 0) and now - since >= every)


def spawn(sid):
    """Start `roost checkpoint <sid>` in the background, detached from the hook."""
    launcher = os.path.realpath(sys.argv[0]) if sys.argv and sys.argv[0] else "roost"
    subprocess.Popen([sys.executable, launcher, "checkpoint", sid], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def run(sid, now=None):
    """Write sid's Handoff from its transcript, without its README. True when one was written."""
    path = attempt_path(sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    try:
        handoff, kinds, spent, _ = summaries.summarize(sid, readme=False, now=now)
    except ValueError as e:
        config.log(f"checkpoint {sid[:8]}: no Handoff written: {e}")
        return False
    config.log(f"checkpoint {sid[:8]}: wrote {handoff} (${spent:.2f})" + (f", redacted {', '.join(kinds)}" if kinds else ""))
    return True


def hook_command(args):
    """roost hook stop: Claude Code's Stop hook. Starts a checkpoint when one is due; never fails the hook."""
    try:
        hook = json.loads(sys.stdin.read())
        if due(hook.get("session_id"), hook.get("transcript_path") or ""):
            spawn(hook["session_id"])
    except Exception as e:
        config.log(f"hook stop: {e!r}")
    return 0


def checkpoint_command(args):
    return 0 if run(args.session) else 1
