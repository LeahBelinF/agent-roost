"""Terminals: which one, opening a Session in a window, and AppleScript."""
import json, os, re, shlex, subprocess, time, uuid
from pathlib import Path

from roost.core.sessiondata import launch_dir
from roost.core.projectdata import find_project
from roost.core.formatting import short
from roost.core.mdfiles import readme_lines
from roost.core import config
from roost.core import projectdata
from roost.core import plugin


def mark_open(sessions, term):
    """Flag background sessions that are open in a window right now."""
    titles, clients = window_titles(term), attach_clients()
    for s in sessions:
        s["attached"] = s["kind"] == "background" and (
            s.get("attach") in clients or any(x.endswith(s["name"]) for x in titles))
    return sessions


def start_plain(cwd, term):
    """Start a Session with no project in cwd: claude, with roost's plugin when it has to be named.
    With a terminal, opens a new window and returns a message; without one, returns what to run here."""
    try:
        argv = ["claude", *plugin.plugin_args()]
    except ValueError:  # no plugin: the Session just goes without roost's skills
        argv = ["claude"]
    if not term:
        return {"start": argv, "cwd": cwd}
    t = TERMINALS[term]
    folder = os.path.basename(cwd.rstrip("/")) or cwd
    if term == "herdr-app":
        if not app_open(folder, folder, cwd, argv):
            return f"Couldn't open a {t['label']} tab (is the Herdr app open?)"
        return f"Started a Session in {short(cwd)}"
    if term == "herdr":
        if not herdr_open(folder, folder, cwd, shlex.join(argv)):
            return f"Couldn't open a {t['label']} tab (is herdr running?)"
        return f"Started a Session in {short(cwd)}"
    if osa(t["new"], cwd, *window_command(shlex.join(argv), folder)) is None:
        return f"Couldn't open a {t['label']} window (check Automation permission, or run roost setup)"
    return f"Started a Session in {short(cwd)}"


def start_session(pid, cwd, term, name=None):
    """Start a fresh Session in cwd whose first prompt is /pickup <pid>, and list it in the project's
    sessions:. Claude Code takes the id roost picks, so roost knows it before the Session starts.
    pid is a project, a Session recap, or a note's path; name is the Session's name (default pid).
    With a terminal, opens a new window and returns a message; without one, returns what to run here."""
    sid, name = str(uuid.uuid4()), name or pid
    argv = ["claude", "--session-id", sid, "-n", name, *plugin.plugin_args(), f"/roost:pickup {pid}"]
    folder = find_project(pid)  # a project's folder, or a Session recap's file
    readme_lines(folder)  # refuse before starting anything if the README (or recap) can't take the Session
    if not term:
        projectdata.assign_session(sid, name, folder)
        return {"start": argv, "cwd": cwd}
    t = TERMINALS[term]
    if term == "herdr-app":
        if not app_open(pid, name, cwd, argv):
            return f"Couldn't open a {t['label']} tab (is the Herdr app open?)"
    elif term == "herdr":
        if not herdr_open(pid, name, cwd, shlex.join(argv)):
            return f"Couldn't open a {t['label']} tab (is herdr running?)"
    elif osa(t["new"], cwd, *window_command(shlex.join(argv), name)) is None:
        return f"Couldn't open a {t['label']} window (check Automation permission, or run roost setup)"
    projectdata.assign_session(sid, name, folder)
    return f"Started a Session for {name} in {short(cwd)}"


# ---------- herdr ----------
# herdr is driven by its CLI, not AppleScript: one workspace per project (labelled with the project
# id), one tab per Session (labelled with the Session's name, which is how roost finds it again).

def herdr(*args):
    """Run `herdr <args>`; the "result" of its JSON output, or None when it fails, is missing, or hangs."""
    try:
        r = subprocess.run(["herdr", *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        config.log(f"herdr {' '.join(args[:2])} failed: {e}")
        return None
    if r.returncode != 0:
        config.log(f"herdr {' '.join(args[:2])} exited {r.returncode}: {r.stderr.strip()[:200]}")
        return None
    if not r.stdout.strip():  # some commands (pane run) print nothing when they work
        return {}
    try:
        return json.loads(r.stdout).get("result")
    except (ValueError, AttributeError):
        config.log(f"herdr {' '.join(args[:2])} printed something other than JSON")
        return None


# herdr's server and the Herdr app's terminals keep the environment they were started in. Started from
# inside a Claude Code session, they hand its markers to every claude run there, and Claude Code then
# takes that claude for a child session and saves no transcript. So claude runs with them unset.
CHILD_SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT",
                      "CLAUDE_CODE_SSE_PORT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_SESSION_ATTENDED",
                      "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_PID")
UNSET_CHILD_SESSION = ["env", *(a for v in CHILD_SESSION_VARS for a in ("-u", v))]


def herdr_open(group, label, cwd, cmd):
    """Run cmd in a new tab labelled `label`, in the workspace labelled `group` (made if missing).
    True when every herdr call worked."""
    listed = herdr("workspace", "list")
    if listed is None:
        return False
    ws = next((w for w in listed.get("workspaces", []) if w.get("label") == group), None)
    if ws:
        made = herdr("tab", "create", "--workspace", ws["workspace_id"], "--cwd", cwd, "--label", label, "--focus")
        if not made or herdr("workspace", "focus", ws["workspace_id"]) is None:
            return False
    else:
        made = herdr("workspace", "create", "--cwd", cwd, "--label", group, "--focus")
        if not made or herdr("tab", "rename", made["tab"]["tab_id"], label) is None:
            return False
    return herdr("pane", "run", made["root_pane"]["pane_id"], f"{shlex.join(UNSET_CHILD_SESSION)} {cmd}") is not None


# ---------- the Herdr desktop app ----------
# A separate program from herdr, driven by its own CLI, herdr-app: one space per project, one tab per
# Session, found again by the tab's name.

def herdr_app(*args):
    """Run `herdr-app <args>`; its JSON output, or None when it fails, is missing, hangs, or reports an error."""
    try:
        r = subprocess.run(["herdr-app", *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        config.log(f"herdr-app {' '.join(args[:2])} failed: {e}")
        return None
    try:
        out = json.loads(r.stdout)
    except ValueError:
        out = None
    if r.returncode != 0 or not isinstance(out, dict) or "error" in out:
        config.log(f"herdr-app {' '.join(args[:2])} exited {r.returncode}: {(r.stdout or r.stderr).strip()[:200]}")
        return None
    return out


def app_spaces():
    listed = herdr_app("ls")
    return None if listed is None else listed.get("spaces", [])


def app_open(group, label, cwd, argv):
    """Run argv in a new tab named `label`, in the space named `group` (made if missing).
    True when every call worked."""
    spaces = app_spaces()
    if spaces is None:
        return False
    argv = [*UNSET_CHILD_SESSION, *argv]
    space = next((s for s in spaces if s.get("name") == group), None)
    if not space:
        return herdr_app("do", "space.new", "--name", group, "--folder", cwd, "--tab", label,
                         "--argv", json.dumps(argv)) is not None
    return herdr_app("do", "tab.new", "--name", label, "--space", space["id"], "--folder", cwd,
                     "--argv", json.dumps(argv)) is not None \
        and herdr_app("do", "space.select", "--id", space["id"]) is not None


def app_tabs():
    """(space, tab) for every tab in the app."""
    return [(s, t) for s in app_spaces() or [] for t in s.get("tabs", [])]


def app_focus(names):
    """Bring forward the tab named with one of names. True when one was found and selected."""
    found = next(((s, t) for s, t in app_tabs() if t.get("name") in names), None)
    return bool(found) and herdr_app("do", "space.select", "--id", found[0]["id"]) is not None \
        and herdr_app("do", "tab.select", "--id", found[1]["id"]) is not None


def read_minimized():
    try:
        return json.loads(config.MINIMIZED.read_text())
    except (OSError, ValueError):
        return {}


def write_minimized(minimized):
    config.MINIMIZED.parent.mkdir(parents=True, exist_ok=True)
    config.MINIMIZED.write_text(json.dumps(minimized, indent=2) + "\n")


def is_minimized(s):
    return s["id"] in read_minimized()


def app_minimize(s):
    """Take s's tab away in the Herdr app, its terminal still running (terminal.park), and remember its
    space so app_restore can bring it back there. True when it was parked."""
    names = [n for n in {s["name"], s.get("ai_title")} if n]
    found = next(((sp, t) for sp, t in app_tabs() if t.get("name") in names and t.get("terminals")), None)
    if not found:
        return False
    space, tab = found
    terminal = tab["terminals"][0]["terminal"]
    if herdr_app("do", "terminal.park", "--terminal", terminal) is None:
        return False
    minimized = read_minimized()
    minimized[s["id"]] = {"terminal": terminal, "space": space["name"], "tab": tab["name"], "folder": space.get("folder")}
    write_minimized(minimized)
    return True


def app_restore(s):
    """Give a minimized Session's terminal a tab again, in its space (made again if parking closed it),
    named as before. True when it's back; a failure keeps the record, so it can be tried again."""
    entry = read_minimized().get(s["id"])
    spaces = app_spaces() if entry else None
    if spaces is None:
        return False
    space = next((sp for sp in spaces if sp.get("name") == entry["space"]), None)
    placeholder = None
    if space:
        space_id = space["id"]
    else:  # a space can't be made empty: make it with a shell, and close that tab once the Session is in
        made = herdr_app("do", "space.new", "--name", entry["space"], "--folder", entry.get("folder") or os.path.expanduser("~"))
        if made is None:
            return False
        space_id, placeholder = made["space"], made["tab"]
    if herdr_app("do", "space.select", "--id", space_id) is None:  # terminal.keep puts the tab in the shown space
        return False
    kept = herdr_app("do", "terminal.keep", "--terminal", entry["terminal"], "--place", "tab")
    if kept is None:
        return False
    if kept.get("tab"):
        herdr_app("do", "tab.rename", "--id", kept["tab"], "--name", entry["tab"])
    if placeholder:
        herdr_app("do", "tab.close", "--id", placeholder, "--confirmed", "true")
    minimized = read_minimized()
    minimized.pop(s["id"], None)
    write_minimized(minimized)
    return True


def herdr_tabs():
    listed = herdr("tab", "list")
    return [] if listed is None else listed.get("tabs", [])


def herdr_focus(names):
    """Bring forward the tab labelled with one of names. True when one was found and focused."""
    tab = next((t for t in herdr_tabs() if t.get("label") in names), None)
    return bool(tab) and herdr("workspace", "focus", tab["workspace_id"]) is not None \
        and herdr("tab", "focus", tab["tab_id"]) is not None


# ---------- terminals ----------
# Each terminal gets two AppleScripts: "new" opens a window in a folder and
# runs a command; "focus" brings forward a window whose title ends with one of
# the given names (Claude Code puts the session name in the title).

TERMINALS = {
    "ghostty": {
        "label": "Ghostty",
        "app": "Ghostty",
        "term_program": "ghostty",
        "titles": 'tell application "Ghostty" to get name of every terminal of every tab of every window',
        "new": """
on run argv
  tell application "Ghostty"
    set cfg to new surface configuration
    set initial working directory of cfg to item 1 of argv
    set command of cfg to item 3 of argv
    new window with configuration cfg
    activate
  end tell
end run
""",
        "focus": """
on run argv
  tell application "Ghostty"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with term in terminals of t
          repeat with n in argv
            if (name of term) ends with (n as text) then
              focus term
              activate
              return "ok"
            end if
          end repeat
        end repeat
      end repeat
    end repeat
  end tell
  return "missing"
end run
""",
    },
    "terminal": {
        "label": "Terminal (macOS)",
        "app": "Terminal",
        "term_program": "apple_terminal",
        "titles": 'tell application "Terminal" to get custom title of every tab of every window',
        "new": """
on run argv
  tell application "Terminal"
    do script "cd " & quoted form of (item 1 of argv) & " && " & (item 2 of argv)
    activate
  end tell
end run
""",
        "focus": """
on run argv
  tell application "Terminal"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with n in argv
          if (custom title of t) contains (n as text) or (name of w) contains (n as text) then
            set selected of t to true
            set index of w to 1
            activate
            return "ok"
          end if
        end repeat
      end repeat
    end repeat
  end tell
  return "missing"
end run
""",
    },
    "iterm2": {
        "label": "iTerm2",
        "app": "iTerm",
        "term_program": "iterm.app",
        "titles": 'tell application "iTerm" to get name of every session of every tab of every window',
        "new": """
on run argv
  tell application "iTerm"
    set w to (create window with default profile)
    tell current session of w to write text "cd " & quoted form of (item 1 of argv) & " && " & (item 2 of argv)
    activate
  end tell
end run
""",
        "focus": """
on run argv
  tell application "iTerm"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          repeat with n in argv
            if (name of s) contains (n as text) then
              select w
              tell t to select
              tell s to select
              activate
              return "ok"
            end if
          end repeat
        end repeat
      end repeat
    end repeat
  end tell
  return "missing"
end run
""",
    },
    "herdr-app": {  # the Herdr desktop app, driven through herdr_app(); detected by HC_TERMINAL_ID
        "label": "Herdr",
        "app": None,
        "term_program": None,
    },
    "herdr": {  # driven through herdr(), not AppleScript; detected by HERDR_ENV, not TERM_PROGRAM
        "label": "herdr",
        "app": None,
        "term_program": None,
    },
}


def detect_terminal():
    if os.environ.get("HC_TERMINAL_ID"):  # the Herdr app also sets TERM_PROGRAM=ghostty, so it comes first
        return "herdr-app"
    if os.environ.get("HERDR_ENV") == "1":  # herdr runs inside another terminal, so it comes first
        return "herdr"
    tp = os.environ.get("TERM_PROGRAM", "").lower()
    return next((k for k, v in TERMINALS.items() if v["term_program"] == tp), None)


# An IDE's terminal can't be scripted to open a tab, so there roost copies the line that runs a
# Session, and says how to open a tab to paste it in.
IDE_NOTES = {
    "jetbrains": "Copied the command: press ⌘T and paste it to open it in a new tab",
    "vscode": "Copied the command: open a new terminal (⌃⇧`) and paste it",
}


def ide_terminal(env=None):
    """The IDE whose terminal roost runs in: "jetbrains" (IntelliJ and the rest), "vscode" (VS Code
    and Cursor), or None."""
    env = os.environ if env is None else env
    if env.get("HERDR_ENV") == "1" or env.get("HC_TERMINAL_ID"):  # herdr can inherit an IDE's variables
        return None
    if env.get("TERMINAL_EMULATOR", "").startswith("JetBrains"):
        return "jetbrains"
    return "vscode" if env.get("TERM_PROGRAM") == "vscode" else None


def run_line(s):
    """The shell line that runs s the way open_session would, to paste in another tab."""
    if s.get("start"):
        return f"cd {shlex.quote(s['cwd'])} && {shlex.join(s['start'])}"
    if s.get("attach"):
        return shlex.join(["claude", "attach", s["attach"]])
    return f"cd {shlex.quote(launch_dir(s))} && {shlex.join(['claude', '--resume', s['id']])}"


def chosen_terminal():
    """The terminal roost opens sessions in: config file first, then auto-detect.
    None means open in the current terminal."""
    cfg = config.read_config()
    if "terminal" not in cfg:
        return detect_terminal()
    return cfg["terminal"] if cfg["terminal"] in TERMINALS else None


def app_running(app):
    # process names vary in case (Ghostty runs as "ghostty"), so match case-insensitively
    name = "iTerm2" if app == "iTerm" else app
    return subprocess.run(["pgrep", "-iq", f"^{name}$"], stdin=subprocess.DEVNULL).returncode == 0


def window_titles(term):
    """Titles of every open terminal tab. Claude doesn't report which background
    sessions are attached to a window, so titles are one way to tell. In herdr, the tab labels."""
    if term == "herdr-app":
        return [t["name"] for _, t in app_tabs() if t.get("name")]
    if term == "herdr":
        return [t["label"] for t in herdr_tabs() if t.get("label")]
    if not term or not app_running(TERMINALS[term]["app"]):
        return []
    out = osa(TERMINALS[term]["titles"]) or ""
    return [x.strip() for x in out.split(",") if x.strip()]


def attach_clients():
    """Short ids of background sessions some window is running `claude attach` on."""
    out = subprocess.run(["ps", "-axo", "command="], stdin=subprocess.DEVNULL, capture_output=True, text=True).stdout
    return set(re.findall(r"claude attach ([0-9a-f]{8})", out))


def osa(script, *args):
    """Run an AppleScript; its output, or None when it fails or hangs (macOS waiting on an Automation prompt)."""
    try:
        r = subprocess.run(["osascript", "-", *args], input=script, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        config.log("osascript took over 10s and was stopped")
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def window_command(cmd, title):
    """Shell line that titles the window after the session, runs cmd, then leaves
    a normal shell. Returns (typed form, direct-exec form); Ghostty uses the
    direct form so its shell integration can't rename the window to the command."""
    line = f"printf '\\033]0;%s\\007' {shlex.quote(title)}; {cmd}"
    shell = os.environ.get("SHELL", "/bin/zsh")
    direct = f"{shell} -lic {shlex.quote(line + f'; exec {shell} -l')}"
    return line, direct


def open_in_window(s, term, group=None):
    """Open the session in a new window of `term` without leaving roost. Returns a status message.
    group is the herdr workspace to open it in (the Session's project); other terminals ignore it."""
    if s.get("desktop"):
        return go_to_desktop(s)
    t = TERMINALS[term]
    if s["kind"] == "interactive" or s.get("attached"):
        names = [n for n in {s["name"], s.get("ai_title")} if n]
        if term == "herdr-app":
            if is_minimized(s):
                return f"Restored {s['name']}" if app_restore(s) else f"Couldn't restore {s['name']} (is the Herdr app open?)"
            found = app_focus(names)
        else:
            found = herdr_focus(names) if term == "herdr" else osa(t["focus"], *names) == "ok"
        if found:
            return f"Switched to {s['name']}, already open"
        return f"{s['name']} is already open, but its window wasn't found"
    cmd = f"claude attach {s['attach']}" if s.get("attach") else f"claude --resume {s['id']}"
    cwd = launch_dir(s)
    if term == "herdr-app":
        argv = ["claude", "attach", s["attach"]] if s.get("attach") else ["claude", "--resume", s["id"]]
        if not app_open(group or os.path.basename(cwd.rstrip("/")) or cwd, s["name"], cwd, argv):
            return f"Couldn't open a {t['label']} tab (is the Herdr app open?)"
    elif term == "herdr":
        if not herdr_open(group or os.path.basename(cwd.rstrip("/")) or cwd, s["name"], cwd, cmd):
            return f"Couldn't open a {t['label']} tab (is herdr running?)"
    elif osa(t["new"], cwd, *window_command(cmd, s["name"])) is None:
        return f"Couldn't open a {t['label']} window (check Automation permission, or run roost setup)"
    s["opening"] = time.time()
    return f"Opened {s['name']} in a new window"


def go_to_desktop(s):
    """Bring Claude Desktop forward, where s is open. Returns a status message."""
    if osa('tell application "Claude" to activate') is None:
        return f"{s['name']} is open in Claude Desktop, but Claude Desktop couldn't be brought forward"
    return f"Switched to Claude Desktop, where {s['name']} is open"


def open_session(s):
    if s.get("start"):  # a fresh Session from a project (start_session)
        os.chdir(s["cwd"])
        os.execvp("claude", s["start"])
    if s.get("attach"):
        os.execvp("claude", ["claude", "attach", s["attach"]])
    os.chdir(launch_dir(s))
    os.execvp("claude", ["claude", "--resume", s["id"]])


def app_installed(app):
    return any(Path(d, f"{app}.app").exists() for d in
               ("/Applications", "/System/Applications/Utilities", str(Path.home() / "Applications")))
