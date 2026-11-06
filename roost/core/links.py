"""A Session's PRs and tickets, for the roost-links mod in the Herdr app: `roost links --terminal <t>`.

PRs: the ones the transcript links (pr-link records), and every PR on each branch the Session ran on,
looked up with gh. Tickets: ClickUp links in what was said and run, and CU-<id> in its branch names and
in the git commit commands it ran. Answers are cached for a short while, since gh is slow."""
import json, re, subprocess, time
from pathlib import Path

from roost.core import config
from roost.core.handoffs import session_transcripts
from roost.core.terminals import terminal_session

LINKS_CACHE = Path.home() / ".cache" / "roost-links"
FRESH = 120  # seconds an answer is reused before gh is asked again

# The ticket tracker, in one place so it can become a config setting: `link` finds a ticket's URL in what
# was said (group 1 is its id), `tag` finds a bare ticket id in branch names and commit messages, and `url`
# makes a tag's id into a link. ClickUp for now.
TICKETS = {
    "link": re.compile(r"https://app\.clickup\.com/t/(?:\d+/)?([A-Za-z0-9][\w-]*)"),
    "tag": re.compile(r"(?<![\w-])[Cc][Uu]-(\d[0-9a-z]{5,11})(?![0-9a-z])"),
    "url": "https://app.clickup.com/t/{id}",
}
PR_URL = re.compile(r"^https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
GITHUB_REMOTE = re.compile(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$")
DEFAULT_BRANCHES = {"", "HEAD", "main", "master"}


def run(cmd, cwd=None):
    """A command's stdout, or None when it fails or can't run."""
    try:
        r = subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def said(rec):
    """The text of a record that counts for tickets: what the person typed, Claude's replies, and the
    tool calls' inputs. Tool results are left out: a search lists many tickets the Session isn't about."""
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return [content]
    parts = []
    for c in content if isinstance(content, list) else []:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "text":
            parts.append(c.get("text") or "")
        elif c.get("type") == "tool_use":
            parts.append(json.dumps(c.get("input") or {}))
    return parts


def scan(sid):
    """What a Session's transcripts say: {"prs": [url], "tickets": {id: url}, "branches": [(cwd, branch)]}."""
    prs, tickets, branches = {}, {}, {}
    for path in session_transcripts(sid):
        with open(path, errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                if rec.get("type") == "pr-link" and rec.get("prUrl"):
                    prs[rec["prUrl"]] = None
                branch, cwd = rec.get("gitBranch") or "", rec.get("cwd")
                if cwd and branch not in DEFAULT_BRANCHES:
                    branches[(cwd, branch)] = None
                    for t in TICKETS["tag"].findall(branch):
                        tickets.setdefault(t, TICKETS["url"].format(id=t))
                if rec.get("type") not in ("user", "assistant"):
                    continue
                for text in said(rec):
                    for m in TICKETS["link"].finditer(text):
                        tickets.setdefault(m.group(1), m.group(0))
                content = (rec.get("message") or {}).get("content")
                for c in content if isinstance(content, list) else []:
                    if not isinstance(c, dict) or c.get("type") != "tool_use" or c.get("name") != "Bash":
                        continue
                    cmd = (c.get("input") or {}).get("command") or ""
                    if "git commit" in cmd:
                        for t in TICKETS["tag"].findall(cmd):
                            tickets.setdefault(t, TICKETS["url"].format(id=t))
    return {"prs": list(prs), "tickets": tickets, "branches": list(branches)}


def repo_of(cwd):
    """The GitHub owner/repo that a folder's origin points at, or None. origin, not gh's default:
    a repo with a stale upstream remote sends a bare `gh pr` to the wrong place."""
    url = run(["git", "-C", cwd, "remote", "get-url", "origin"])
    m = GITHUB_REMOTE.search((url or "").strip())
    return m.group(1) if m else None


def pr_entry(p, repo):
    return {"repo": repo, "number": p.get("number"), "url": p.get("url"), "state": (p.get("state") or "").lower(),
            "title": p.get("title") or ""}


def branch_prs(branches):
    """Every PR on each (folder, branch), in any state."""
    found, asked = [], set()
    for cwd, branch in branches:
        repo = repo_of(cwd) if Path(cwd).is_dir() else None
        if not repo or (repo, branch) in asked:
            continue
        asked.add((repo, branch))
        out = run(["gh", "pr", "list", "-R", repo, "--head", branch, "--state", "all", "--limit", "20",
                   "--json", "number,url,state,title"])
        try:
            found += [pr_entry(p, repo) for p in json.loads(out or "[]")]
        except ValueError:
            continue
    return found


def linked_pr(url):
    """A PR the transcript linked, with its state from gh (unknown when gh can't say)."""
    m = PR_URL.match(url)
    if not m:
        return None
    out = run(["gh", "pr", "view", m.group(2), "-R", m.group(1), "--json", "number,url,state,title"])
    try:
        return pr_entry(json.loads(out), m.group(1))
    except (TypeError, ValueError):
        return {"repo": m.group(1), "number": int(m.group(2)), "url": url, "state": "", "title": ""}


def gather_links(sid):
    """A Session's PRs (newest number first within each repo) and tickets."""
    s = scan(sid)
    prs = {p["url"]: p for p in branch_prs(s["branches"])}
    for url in s["prs"]:
        if url not in prs:
            p = linked_pr(url)
            if p:
                prs[url] = p
    return {"session": sid,
            "prs": sorted(prs.values(), key=lambda p: (p["repo"], -(p["number"] or 0))),
            "tickets": [{"id": t, "url": u} for t, u in s["tickets"].items()]}


def cached_links(sid, now=None):
    """gather_links(sid), from the cache while it's fresh."""
    now = time.time() if now is None else now
    path = LINKS_CACHE / f"{sid}.json"
    try:
        cached = json.loads(path.read_text())
        if now - cached["at"] < FRESH:
            return cached["links"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    found = gather_links(sid)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"at": now, "links": found}))
    except OSError as e:
        config.log(f"links cache: {e}")
    return found


def links_flags(p):
    who = p.add_mutually_exclusive_group(required=True)
    who.add_argument("--terminal", help="a Herdr terminal's address (hc ls): the Session running in it")
    who.add_argument("--session", help="a Session id")


def links_command(args):
    """Print a Session's PRs and tickets as JSON; {"session": null} when no Session runs there."""
    sid = args.session or terminal_session(args.terminal)
    if not sid or not session_transcripts(sid):
        print(json.dumps({"session": sid if sid else None, "prs": [], "tickets": []}))
        return 0
    try:
        print(json.dumps(cached_links(sid)))
    except Exception as e:
        config.log(f"links {sid}: {e}")
        print(json.dumps({"session": sid, "prs": [], "tickets": [], "error": str(e)}))
        return 1
    return 0
