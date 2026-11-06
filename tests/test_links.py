from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import own_tests  # noqa: F401
from roost.core import links as linksmod


def said(ts, text, **rec):
    return dict({"type": "user", "timestamp": ts, "message": {"content": text}}, **rec)


class Links(unittest.TestCase):
    """roost links: a Session's PRs and tickets, for the Herdr app's roost-links mod."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.saved = (roost.PROJECTS, linksmod.LINKS_CACHE, linksmod.CLAUDE_SESSIONS, linksmod.run, roost.LOG)
        roost.PROJECTS, linksmod.LINKS_CACHE, linksmod.CLAUDE_SESSIONS = base / "t", base / "cache", base / "sessions"
        roost.LOG = base / "roost.log"
        self.calls, self.answers = [], {}
        linksmod.run = self.fake_run

    def tearDown(self):
        roost.PROJECTS, linksmod.LINKS_CACHE, linksmod.CLAUDE_SESSIONS, linksmod.run, roost.LOG = self.saved
        self.tmp.cleanup()

    def fake_run(self, cmd, cwd=None):
        self.calls.append(cmd)
        return next((out for key, out in self.answers.items() if tuple(cmd[:len(key)]) == key), None)

    def transcript(self, records, sid=SID):
        path = roost.PROJECTS / "-w" / f"{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in records))

    def test_tickets_come_from_what_was_said_branches_and_commits_not_tool_results(self):
        self.transcript([
            said("1", "look at https://app.clickup.com/t/868abc123 please", cwd="/w", gitBranch="f/CU-868brn001-x"),
            reply("2", "m1", USAGE, [{"type": "text", "text": "and https://app.clickup.com/t/9010051985/868team01"},
                                     bash("b1", "git commit -m 'Fix it (CU-868cmt001)'"),
                                     bash("b2", "echo CU-868echo01")]),
            {"type": "user", "timestamp": "3", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "b2", "content": "https://app.clickup.com/t/868noise1"}]}},
        ])
        s = linksmod.scan(SID)
        self.assertEqual(s["tickets"], {
            "868brn001": "https://app.clickup.com/t/868brn001",
            "868abc123": "https://app.clickup.com/t/868abc123",
            "868team01": "https://app.clickup.com/t/9010051985/868team01",
            "868cmt001": "https://app.clickup.com/t/868cmt001"})
        self.assertEqual(s["branches"], [("/w", "f/CU-868brn001-x")])

    def test_default_branches_are_not_asked_about(self):
        self.transcript([said("1", "hi", cwd="/w", gitBranch="main"), said("2", "hi", cwd="/w", gitBranch="HEAD")])
        self.assertEqual(linksmod.scan(SID)["branches"], [])

    def test_prs_on_its_branches_and_the_ones_it_linked(self):
        folder = Path(self.tmp.name) / "repo"
        folder.mkdir()
        self.transcript([said("1", "go", cwd=str(folder), gitBranch="f/x"),
                         {"type": "pr-link", "prUrl": "https://github.com/o/r/pull/3", "timestamp": "2"},
                         {"type": "pr-link", "prUrl": "https://github.com/o/r/pull/9", "timestamp": "3"}])
        self.answers = {
            ("git", "-C", str(folder), "remote", "get-url", "origin"): "git@github.com:o/r.git\n",
            ("gh", "pr", "list"): json.dumps([{"number": 9, "url": "https://github.com/o/r/pull/9", "state": "MERGED", "title": "Nine"}]),
            ("gh", "pr", "view", "3"): json.dumps({"number": 3, "url": "https://github.com/o/r/pull/3", "state": "OPEN", "title": "Three"}),
        }
        found = linksmod.gather_links(SID)
        self.assertEqual([(p["number"], p["state"], p["title"]) for p in found["prs"]], [(9, "merged", "Nine"), (3, "open", "Three")])
        listed = next(c for c in self.calls if c[:3] == ["gh", "pr", "list"])
        self.assertEqual(listed[3:7], ["-R", "o/r", "--head", "f/x"])  # origin's repo, never gh's default remote
        self.assertNotIn(["gh", "pr", "view", "9"], [c[:4] for c in self.calls])  # the branch listing already had it

    def test_a_linked_pr_gh_cant_read_still_shows(self):
        self.transcript([{"type": "pr-link", "prUrl": "https://github.com/o/r/pull/4", "timestamp": "1"}])
        self.assertEqual(linksmod.gather_links(SID)["prs"],
                         [{"repo": "o/r", "number": 4, "url": "https://github.com/o/r/pull/4", "state": "", "title": ""}])

    def test_answers_are_cached_while_fresh(self):
        self.transcript([{"type": "pr-link", "prUrl": "https://github.com/o/r/pull/4", "timestamp": "1"}])
        linksmod.cached_links(SID, now=1000)
        n = len(self.calls)
        linksmod.cached_links(SID, now=1000 + linksmod.FRESH - 1)
        self.assertEqual(len(self.calls), n)
        linksmod.cached_links(SID, now=1000 + linksmod.FRESH)
        self.assertGreater(len(self.calls), n)

    def test_a_terminal_s_session_comes_from_its_process_then_its_argv(self):
        linksmod.CLAUDE_SESSIONS.mkdir()
        (linksmod.CLAUDE_SESSIONS / "42.json").write_text(json.dumps({"pid": 42, "sessionId": "after-clear"}))
        self.answers = {("hc", "get", "t1"): json.dumps({"foreground": {"pid": 42, "argv": ["claude", "--session-id", "first"]}}),
                        ("hc", "get", "t2"): json.dumps({"foreground": {"pid": 7, "argv": ["claude", "--resume", "old"]}}),
                        ("hc", "get", "t3"): json.dumps({"foreground": {"pid": 8, "argv": ["zsh"]}})}
        self.assertEqual(linksmod.terminal_session("t1"), "after-clear")
        self.assertEqual(linksmod.terminal_session("t2"), "old")
        self.assertIsNone(linksmod.terminal_session("t3"))
        self.assertIsNone(linksmod.terminal_session("gone"))

    def test_the_command_says_when_no_session_runs_there(self):
        self.answers = {("hc", "get", "t3"): json.dumps({"foreground": {"pid": 8, "argv": ["zsh"]}})}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = linksmod.links_command(__import__('argparse').Namespace(terminal="t3", session=None))
        self.assertEqual((code, json.loads(out.getvalue())), (0, {"session": None, "prs": [], "tickets": []}))


class HerdrMod(unittest.TestCase):
    """roost setup herdr copies the roost-links mod into the Herdr app's mods."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = os.environ.get("HERDR_APP_HOME")
        os.environ["HERDR_APP_HOME"] = self.tmp.name

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("HERDR_APP_HOME", None)
        else:
            os.environ["HERDR_APP_HOME"] = self.saved
        self.tmp.cleanup()

    def test_it_copies_the_mod(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(roost.setup_herdr(), 0)
        dest = Path(self.tmp.name) / "mods" / "roost-links"
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ["main.js", "mod.json"])
        self.assertIn('"links", "--terminal"', (dest / "main.js").read_text())

    def test_no_herdr_app_no_copy(self):
        os.environ["HERDR_APP_HOME"] = str(Path(self.tmp.name) / "missing")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(roost.setup_herdr(), 1)
        self.assertFalse((Path(self.tmp.name) / "missing").exists())


if __name__ == "__main__":
    unittest.main()
