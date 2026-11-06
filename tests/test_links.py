from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import own_tests  # noqa: F401
import argparse
import builtins
import shutil
import unittest.mock
from roost.core import links as linksmod
from roost.core import install as installmod
from roost.core import terminals as terminalsmod

load_tests = own_tests(__name__)

CU = "https://app.clickup.com/t/"


def said_by_person(text, cwd="/nowhere", branch=""):
    return {"type": "user", "timestamp": "2026-10-09T10:00:00.000Z", "sessionId": SID, "cwd": cwd,
            "gitBranch": branch, "message": {"content": text}}


def said_by_claude(content, cwd="/nowhere", branch=""):
    rec = reply("2026-10-09T10:01:00.000Z", "m1", USAGE, content)
    rec.update(cwd=cwd, gitBranch=branch)
    return rec


def tool_result(tid, text, cwd="/nowhere", branch=""):
    rec = result("2026-10-09T10:02:00.000Z", tid, text)
    rec.update(cwd=cwd, gitBranch=branch)
    return rec


class Links(unittest.TestCase):
    """roost links: a Session's PRs and tickets as JSON, for the Herdr mod's chips."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.saved_projects = roost.PROJECTS
        roost.PROJECTS = base / "transcripts"
        cache = unittest.mock.patch.object(linksmod, "LINKS_CACHE", base / "links-cache")
        cache.start()
        self.addCleanup(cache.stop)
        self.cache = base / "links-cache"
        self.calls = []
        self.remotes = {}   # folder -> origin url
        self.listed = {}    # (repo, branch) -> [pr dicts as gh prints them]
        self.viewed = {}    # (repo, number) -> pr dict as gh prints it
        runner = unittest.mock.patch.object(linksmod, "run", side_effect=self.fake_run)
        runner.start()
        self.addCleanup(runner.stop)
        self.a = base / "widgets"
        self.b = base / "gadgets"
        self.c = base / "plain"
        for d in (self.a, self.b, self.c):
            d.mkdir()

    def tearDown(self):
        roost.PROJECTS = self.saved_projects
        self.tmp.cleanup()

    def fake_run(self, cmd, cwd=None):
        self.calls.append(list(cmd))
        if cmd[:2] == ["git", "-C"] and cmd[3:] == ["remote", "get-url", "origin"]:
            url = self.remotes.get(cmd[2])
            return url + "\n" if url else None
        if cmd[:3] == ["gh", "pr", "list"]:
            repo, head = cmd[cmd.index("-R") + 1], cmd[cmd.index("--head") + 1]
            return json.dumps(self.listed.get((repo, head), []))
        if cmd[:3] == ["gh", "pr", "view"]:
            p = self.viewed.get((cmd[cmd.index("-R") + 1], int(cmd[3])))
            return json.dumps(p) if p else None
        return None

    def transcript(self, records, sid=SID, sub=None):
        d = roost.PROJECTS / "-w"
        path = d / f"{sid}.jsonl" if sub is None else d / sid / "subagents" / f"{sub}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in records))

    def gh_calls(self, verb):
        return [c for c in self.calls if c[:3] == ["gh", "pr", verb]]

    # tickets

    def test_tickets_come_from_what_was_typed_said_and_run_but_not_tool_results(self):
        self.transcript([
            said_by_person(f"please look at {CU}86aa11 first"),
            said_by_claude([{"type": "text", "text": f"Also related: https://app.clickup.com/t/9000001/86bb22 and {CU}86aa11"}]),
            said_by_claude([bash("b1", f"curl -s {CU}86cc33"),
                            {"type": "tool_use", "id": "w1", "name": "WebFetch", "input": {"url": f"{CU}86dd44"}}]),
            tool_result("b1", f"search results: {CU}86ee55 {CU}86ff66"),
        ])
        got = linksmod.gather_links(SID)["tickets"]
        self.assertEqual(got, [
            {"id": "86aa11", "url": f"{CU}86aa11"},
            {"id": "86bb22", "url": "https://app.clickup.com/t/9000001/86bb22"},
            {"id": "86cc33", "url": f"{CU}86cc33"},
            {"id": "86dd44", "url": f"{CU}86dd44"},
        ])

    def test_cu_ids_count_in_branches_and_git_commit_commands_only(self):
        self.transcript([
            said_by_person("go", cwd=str(self.c), branch="feature/CU-86br01-tidy"),
            said_by_claude([bash("b1", "git commit -m 'Tidy things (cu-86cm02)'"),
                            bash("b2", "echo CU-86no03 && git status"),
                            bash("b3", "git commit -m 'CU-abc123 is not a ticket id'")], cwd=str(self.c), branch="feature/CU-86br01-tidy"),
            said_by_person("CU-86no04 typed, not a link", cwd=str(self.c), branch="feature/CU-86br01-tidy"),
        ])
        got = linksmod.gather_links(SID)["tickets"]
        self.assertEqual(got, [{"id": "86br01", "url": f"{CU}86br01"}, {"id": "86cm02", "url": f"{CU}86cm02"}])

    def test_tickets_in_a_subagent_transcript_count(self):
        self.transcript([said_by_person("go")])
        self.transcript([said_by_claude([{"type": "text", "text": f"see {CU}86sb07"}])], sub="agent-1")
        self.assertEqual(linksmod.gather_links(SID)["tickets"], [{"id": "86sb07", "url": f"{CU}86sb07"}])

    # PRs

    def test_prs_from_pr_links_and_every_branch_the_session_ran_on(self):
        self.remotes = {str(self.a): "git@github.com:acme-oss/widgets.git",
                        str(self.b): "https://github.com/acme-oss/gadgets.git",
                        str(self.c): "git@github.com:acme-oss/plain.git"}
        self.listed = {
            ("acme-oss/widgets", "topic-one"): [
                {"number": 12, "url": "https://github.com/acme-oss/widgets/pull/12", "state": "MERGED", "title": "First go"},
                {"number": 15, "url": "https://github.com/acme-oss/widgets/pull/15", "state": "OPEN", "title": "Second go"}],
            ("acme-oss/gadgets", "fix-two"): [
                {"number": 4, "url": "https://github.com/acme-oss/gadgets/pull/4", "state": "CLOSED", "title": "Gadget fix"}],
        }
        self.viewed = {("other-org/tools", 3): {"number": 3, "url": "https://github.com/other-org/tools/pull/3",
                                                 "state": "OPEN", "title": "Tool change"}}
        self.transcript([
            said_by_person("go", cwd=str(self.a), branch="topic-one"),
            said_by_person("and here", cwd=str(self.b), branch="fix-two"),
            *[said_by_person("default", cwd=str(self.c), branch=b) for b in ("main", "master", "HEAD", "")],
            {"type": "pr-link", "prUrl": "https://github.com/acme-oss/widgets/pull/12"},
            {"type": "pr-link", "prUrl": "https://github.com/other-org/tools/pull/3"},
        ])
        got = linksmod.gather_links(SID)
        self.assertEqual(got["session"], SID)
        self.assertEqual(got["prs"], [
            {"repo": "acme-oss/gadgets", "number": 4, "url": "https://github.com/acme-oss/gadgets/pull/4", "state": "closed", "title": "Gadget fix"},
            {"repo": "acme-oss/widgets", "number": 15, "url": "https://github.com/acme-oss/widgets/pull/15", "state": "open", "title": "Second go"},
            {"repo": "acme-oss/widgets", "number": 12, "url": "https://github.com/acme-oss/widgets/pull/12", "state": "merged", "title": "First go"},
            {"repo": "other-org/tools", "number": 3, "url": "https://github.com/other-org/tools/pull/3", "state": "open", "title": "Tool change"},
        ])
        lists = self.gh_calls("list")
        heads = sorted((c[c.index("-R") + 1], c[c.index("--head") + 1]) for c in lists)
        self.assertEqual(heads, [("acme-oss/gadgets", "fix-two"), ("acme-oss/widgets", "topic-one")])  # no default branches
        for c in lists:
            self.assertEqual(c[c.index("--state") + 1], "all")
        views = self.gh_calls("view")
        self.assertEqual([(c[3], c[c.index("-R") + 1]) for c in views], [("3", "other-org/tools")])  # #12 came from its branch
        for c in self.gh_calls("list") + views:
            self.assertIn("-R", c, "gh must be pointed at origin's repo, never its default remote")

    def test_a_linked_pr_gh_cant_describe_is_still_listed(self):
        self.transcript([said_by_person("go"), {"type": "pr-link", "prUrl": "https://github.com/other-org/tools/pull/9"}])
        self.assertEqual(linksmod.gather_links(SID)["prs"], [
            {"repo": "other-org/tools", "number": 9, "url": "https://github.com/other-org/tools/pull/9", "state": "", "title": ""}])

    # cache

    def test_cached_links_reuses_a_fresh_answer_and_asks_again_once_stale(self):
        self.transcript([said_by_person(f"{CU}86ca01")])
        first = linksmod.cached_links(SID, now=1000.0)
        self.assertEqual(first["tickets"], [{"id": "86ca01", "url": f"{CU}86ca01"}])
        self.assertTrue(any(p.is_file() for p in self.cache.rglob("*")), "the answer is stored under LINKS_CACHE")
        self.transcript([said_by_person(f"{CU}86ca01 {CU}86ca02")])
        self.assertEqual(linksmod.cached_links(SID, now=1000.0 + linksmod.FRESH - 1), first)
        later = linksmod.cached_links(SID, now=1000.0 + linksmod.FRESH + 1)
        self.assertEqual([t["id"] for t in later["tickets"]], ["86ca01", "86ca02"])

    # the command

    def command(self, **kw):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = linksmod.links_command(argparse.Namespace(**{"session": None, "terminal": None, **kw}))
        return code, json.loads(out.getvalue())

    def test_links_command_prints_a_sessions_links(self):
        self.transcript([said_by_person(f"{CU}86lc01")])
        code, got = self.command(session=SID)
        self.assertEqual(code, 0)
        self.assertEqual(got, {"session": SID, "prs": [], "tickets": [{"id": "86lc01", "url": f"{CU}86lc01"}]})

    def test_links_command_finds_the_session_in_a_terminal(self):
        self.transcript([said_by_person(f"{CU}86lc02")])
        asked = []
        with unittest.mock.patch.object(linksmod, "terminal_session", side_effect=lambda a: asked.append(a) or SID):
            code, got = self.command(terminal="w1:t2")
        self.assertEqual(asked, ["w1:t2"])
        self.assertEqual(code, 0)
        self.assertEqual(got["session"], SID)
        self.assertEqual(got["tickets"], [{"id": "86lc02", "url": f"{CU}86lc02"}])

    def test_links_command_with_no_session_in_the_terminal(self):
        with unittest.mock.patch.object(linksmod, "terminal_session", return_value=None):
            code, got = self.command(terminal="w1:t3")
        self.assertEqual(code, 0)
        self.assertEqual(got, {"session": None, "prs": [], "tickets": []})


def bars(node):
    """How many roost-links bars a layout tree holds."""
    here = 1 if node.get("type") == "block" and (node.get("content") or {}).get("mod") == "roost-links" else 0
    return here + sum(bars(c) for c in node.get("children") or [])


def is_bar(node):
    return node.get("type") == "block" and (node.get("content") or {}).get("mod") == "roost-links"


MAIN = {"id": "root", "type": "row", "children": [
    {"id": "side", "type": "block", "content": {"mod": "sidebar"}},
    {"id": "work", "type": "column", "children": [{"id": "tabs", "type": "block", "content": {"mod": "tabs"}}]}]}
MINE = {"id": "root", "type": "column", "children": [{"id": "tabs", "type": "block", "content": {"mod": "tabs"}}]}


class SetupHerdr(unittest.TestCase):
    """roost setup herdr: the roost-links mod copied into Herdr, and its bar in the shown layout, asked first."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "herdr-app"
        (self.home / "layouts").mkdir(parents=True)
        (self.home / "installed.json").write_text(json.dumps({"layout:main": {"v": 1}, "mod:sidebar": {"v": 1}}))
        self.write_layout("main", MAIN)
        env = unittest.mock.patch.dict(os.environ, {"HERDR_APP_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.status = {"layout": "main"}
        self.app_calls = []
        app = unittest.mock.patch.object(terminalsmod, "herdr_app", side_effect=self.herdr_app)
        app.start()
        self.addCleanup(app.stop)
        self.answers = ["y"]
        self.prompts = []
        ask = unittest.mock.patch.object(builtins, "input", side_effect=self.input)
        ask.start()
        self.addCleanup(ask.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def herdr_app(self, *args):
        self.app_calls.append(args)
        if args == ("status",):
            return self.status
        if args[:2] == ("do", "layout.clone"):
            a = json.loads(args[2])
            shutil.copyfile(self.home / "layouts" / f"{a['name']}.json", self.home / "layouts" / f"{a['as']}.json")
            self.status = {"layout": a["as"]}
            return {}
        if args[:2] == ("do", "layout.switch"):
            self.status = {"layout": json.loads(args[2])["name"]}
            return {}
        return None

    def input(self, prompt=""):
        self.prompts.append(prompt)
        a = self.answers.pop(0) if self.answers else "n"
        if a is EOFError:
            raise EOFError
        return a

    def write_layout(self, name, tree):
        (self.home / "layouts" / f"{name}.json").write_text(json.dumps(tree, indent=2) + "\n")

    def layout(self, name):
        return json.loads((self.home / "layouts" / f"{name}.json").read_text())

    def snapshot(self):
        return {p.name: p.read_bytes() for p in sorted((self.home / "layouts").iterdir())}

    def does(self):
        return [c for c in self.app_calls if c and c[0] == "do"]

    def setup(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = installmod.setup_herdr()
        self.out = out.getvalue()
        return code

    # the mod

    def test_copies_the_mods_files_but_not_the_ones_herdr_generates(self):
        src = Path(self.tmp.name) / "mod-src"
        src.mkdir()
        for name, text in [("main.js", "// mod\n"), ("mod.json", '{"id": "roost-links"}\n'),
                           ("jsconfig.json", "{}\n"), ("herdr.d.ts", "declare const x: 1;\n")]:
            (src / name).write_text(text)
        self.status = None
        with unittest.mock.patch.object(installmod, "HERDR_MOD", src):
            self.setup()
        dest = self.home / "mods" / "roost-links"
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ["main.js", "mod.json"])
        self.assertEqual((dest / "main.js").read_text(), "// mod\n")

    def test_copies_roosts_own_mod(self):
        self.status = None
        self.setup()
        dest = self.home / "mods" / "roost-links"
        pkg = ROOT / "roost" / "herdr" / "roost-links"
        for name in ("main.js", "mod.json"):
            self.assertEqual((dest / name).read_bytes(), (pkg / name).read_bytes())

    def test_no_herdr_folder_creates_nothing(self):
        missing = Path(self.tmp.name) / "no-herdr-here"
        with unittest.mock.patch.dict(os.environ, {"HERDR_APP_HOME": str(missing)}):
            self.assertEqual(self.setup(), 1)
        self.assertFalse(missing.exists())

    def test_herdr_not_running_copies_the_mod_and_touches_no_layout(self):
        self.status = None
        before = self.snapshot()
        self.assertEqual(self.setup(), 1)
        self.assertTrue((self.home / "mods" / "roost-links" / "main.js").is_file())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.does(), [])
        self.assertRegex(self.out, r"Herdr isn't running|Herdr is not running")

    # a shipped layout

    def test_shipped_layout_asks_then_copies_it_and_adds_the_bar_to_the_copy(self):
        main_before = (self.home / "layouts" / "main.json").read_bytes()
        self.setup()
        self.assertEqual(len(self.prompts), 1, self.prompts)
        self.assertIn("main", self.prompts[0])
        self.assertIn("main-roost", self.prompts[0])
        clones = [c for c in self.does() if c[1] == "layout.clone"]
        self.assertEqual([json.loads(c[2]) for c in clones], [{"name": "main", "as": "main-roost"}])
        copy = self.layout("main-roost")
        self.assertEqual(bars(copy), 1)
        self.assertTrue(is_bar(copy["children"][-1]["children"][0]), "the bar goes at the top of the work column")
        self.assertEqual((self.home / "layouts" / "main.json").read_bytes(), main_before, "Herdr's own layout is never edited")

    def test_yes_in_any_case_agrees(self):
        for answer in ("y", "Y", "yes", "YES", "Yes"):
            with self.subTest(answer=answer):
                (self.home / "layouts" / "main-roost.json").unlink(missing_ok=True)
                self.status, self.app_calls, self.prompts, self.answers = {"layout": "main"}, [], [], [answer]
                self.setup()
                self.assertEqual(len(self.prompts), 1)
                self.assertEqual([c[1] for c in self.does()], ["layout.clone"])

    def test_shipped_layout_answered_no_changes_nothing_and_prints_the_bar(self):
        for answer in ("n", "no", "", "maybe", EOFError):
            with self.subTest(answer=answer):
                self.app_calls, self.prompts, self.answers = [], [], [answer]
                before = self.snapshot()
                self.assertEqual(self.setup(), 0)
                self.assertEqual(len(self.prompts), 1)
                self.assertEqual(self.does(), [])
                self.assertEqual(self.snapshot(), before)
                self.assertIn("roost-links", self.out)

    def test_existing_copy_asks_to_switch_and_yes_switches(self):
        with_bar = json.loads(json.dumps(MAIN))
        with_bar["children"][-1]["children"].insert(0, {"id": "roost-links", "type": "block", "content": {"mod": "roost-links"}})
        self.write_layout("main-roost", with_bar)
        before = self.snapshot()
        self.setup()
        self.assertEqual(len(self.prompts), 1, self.prompts)
        self.assertIn("main-roost", self.prompts[0])
        self.assertEqual([(c[1], json.loads(c[2])) for c in self.does()], [("layout.switch", {"name": "main-roost"})])
        self.assertEqual(self.snapshot(), before)

    def test_existing_copy_without_the_bar_gets_it_on_yes(self):
        self.write_layout("main-roost", MAIN)
        self.setup()
        self.assertEqual(len(self.prompts), 1, self.prompts)
        self.assertEqual([c[1] for c in self.does()], ["layout.switch"])
        self.assertEqual(bars(self.layout("main-roost")), 1)
        self.assertEqual(bars(self.layout("main")), 0)

    def test_existing_copy_answered_no_changes_nothing(self):
        self.write_layout("main-roost", MAIN)
        self.answers = ["n"]
        before = self.snapshot()
        self.setup()
        self.assertEqual(len(self.prompts), 1, self.prompts)
        self.assertEqual(self.does(), [])
        self.assertEqual(self.snapshot(), before)

    def test_rerunning_on_a_shipped_layout_never_adds_a_second_bar(self):
        self.answers = ["y", "y"]
        self.setup()
        self.status = {"layout": "main"}  # shown again, as after Herdr restarts
        self.setup()
        self.assertEqual(bars(self.layout("main-roost")), 1)
        self.assertEqual(len([c for c in self.does() if c[1] == "layout.clone"]), 1)

    # the person's own layout

    def test_own_layout_asks_then_adds_the_bar_in_place(self):
        self.write_layout("mine", MINE)
        self.status = {"layout": "mine"}
        self.setup()
        self.assertEqual(len(self.prompts), 1, self.prompts)
        self.assertIn("mine", self.prompts[0])
        got = self.layout("mine")
        self.assertEqual(bars(got), 1)
        self.assertTrue(is_bar(got["children"][0]), "the bar goes at the top of a column root")
        self.assertEqual(self.does(), [])

    def test_own_layout_answered_no_is_left_byte_for_byte(self):
        self.write_layout("mine", MINE)
        self.status = {"layout": "mine"}
        before = (self.home / "layouts" / "mine.json").read_bytes()
        for answer in ("n", "", EOFError):
            with self.subTest(answer=answer):
                self.prompts, self.answers = [], [answer]
                self.assertEqual(self.setup(), 0)
                self.assertEqual(len(self.prompts), 1)
                self.assertEqual((self.home / "layouts" / "mine.json").read_bytes(), before)
                self.assertIn("roost-links", self.out)

    def test_a_layout_that_has_the_bar_is_left_alone_and_nothing_is_asked(self):
        has = json.loads(json.dumps(MINE))
        has["children"].insert(0, {"id": "roost-links", "type": "block", "content": {"mod": "roost-links"}})
        self.write_layout("mine", has)
        self.status = {"layout": "mine"}
        before = self.snapshot()
        self.setup()
        self.assertEqual(self.prompts, [])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.does(), [])

    def test_rerunning_on_own_layout_never_adds_a_second_bar(self):
        self.write_layout("mine", MAIN)
        self.status = {"layout": "mine"}
        self.answers = ["y", "y"]
        self.setup()
        self.setup()
        self.assertEqual(bars(self.layout("mine")), 1)


if __name__ == "__main__":
    unittest.main()
