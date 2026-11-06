from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import _SCRATCH, own_tests  # noqa: F401
from test_projects import Projects, ProjectRows, SubProjects  # for their helpers; their tests run in test_projects.py
from test_screens import TextualScreens  # for its helpers; its tests run in test_screens.py

load_tests = own_tests(__name__)


class Sections(unittest.TestCase):
    def test_group_order_is_open_pinned_background_closed(self):
        rows = [
            session("closed", state="closed", kind=None),
            session("background"),
            session("pinned", pinned=True),
            session("interactive", kind="interactive"),
            session("pinned but open", pinned=True, attached=True),
        ]
        self.assertEqual([roost.group(s) for s in rows], [3, 2, 1, 0, 0])

    def test_running_view_hides_closed(self):
        rows = roost.visible([session("a"), session("b", state="closed", kind=None)], RUNNING, "")
        self.assertEqual([s["name"] for s in rows], ["a"])

    def test_sections_stay_in_order_whatever_the_sort(self):
        rows = [session("zeta", kind="interactive"), session("alpha"), session("mid", pinned=True)]
        for col in roost.SORTS:
            for desc in (False, True):
                got = roost.visible(rows, RUNNING, "", (col, desc))  # all three are running
                self.assertEqual([roost.group(s) for s in got], sorted(roost.group(s) for s in got), (col, desc))


class Sorting(unittest.TestCase):
    def test_default_is_most_recent_first(self):
        rows = roost.visible([session("old", mins=50), session("new", mins=1)], RUNNING, "")
        self.assertEqual([s["name"] for s in rows], ["new", "old"])

    def test_sort_by_context_descending(self):
        rows = [session("small", tokens=10), session("big", tokens=900)]
        got = roost.visible(rows, RUNNING, "", ("CONTEXT", True))
        self.assertEqual([s["name"] for s in got], ["big", "small"])

    def test_sort_by_name_ascending_ignores_case(self):
        rows = [session("bravo"), session("Alpha")]
        got = roost.visible(rows, RUNNING, "", ("NAME", False))
        self.assertEqual([s["name"] for s in got], ["Alpha", "bravo"])


class Views(unittest.TestCase):
    """The Sessions entry is a simple history: Running and Closed, and nothing else."""
    def test_the_views_are_running_then_closed_then_unfiled(self):
        self.assertEqual(roost.VIEWS, ["Running", "Closed", "Unfiled"])

    def test_every_session_is_in_exactly_one_view(self):
        """Running: every Session that isn't closed, and one roost is wrapping up (keep). Closed: every
        other closed Session, however long ago it closed and however big it is."""
        now = time.time()
        rows = [session("idle"), session("asks", state="waiting"), session("works", state="busy"),
                dict(session("wrapping", state="closed", kind=None), id="keep-0000"),
                session("just closed", state="closed", kind=None),
                session("closed weeks ago", state="closed", kind=None, mins=60 * 24 * 40),
                dict(session("big and cold", state="closed", kind=None, tokens=900_000), last=now - 9 * 86400)]
        keep = {"keep-0000"}
        running, closed = roost.VIEWS.index("Running"), roost.VIEWS.index("Closed")
        for s in rows:
            views = [roost.VIEWS[i] for i in range(len(roost.VIEWS)) if roost.in_view(s, i, keep)]
            want = "Closed" if s["state"] == "closed" and s["id"] not in keep else "Running"
            self.assertEqual(views, [want], s["name"])
        self.assertEqual({s["name"] for s in roost.visible(rows, running, "", keep=keep)}, {"idle", "asks", "works", "wrapping"})
        self.assertEqual({s["name"] for s in roost.visible(rows, closed, "", keep=keep)},
                         {"just closed", "closed weeks ago", "big and cold"})

    def test_closed_lists_newest_first_under_the_default_sort(self):
        rows = [session("middle", state="closed", kind=None, mins=60 * 24 * 3),
                session("newest", state="closed", kind=None, mins=5),
                session("oldest", state="closed", kind=None, mins=60 * 24 * 30), session("running", mins=1)]
        got = roost.visible(rows, roost.VIEWS.index("Closed"), "")
        self.assertEqual([s["name"] for s in got], ["newest", "middle", "oldest"])

    def starting_view(self, sessions):
        """The view setup() opens Sessions on, by its label."""
        import unittest.mock
        ui = object.__new__(roost.UI)
        with unittest.mock.patch.multiple(roost, read_config=lambda: {}):
            ui.setup(sessions, "")
        return roost.VIEWS[ui.view]

    def test_roost_opens_sessions_on_running_when_one_is_running_else_on_closed(self):
        closed = [session("gone", state="closed", kind=None), session("long gone", state="closed", kind=None, mins=60 * 24 * 20)]
        self.assertEqual(self.starting_view(closed + [session("live", state="waiting")]), "Running")
        self.assertEqual(self.starting_view(closed), "Closed")


class Search(unittest.TestCase):
    def test_all_words_must_match(self):
        s = session("Vendor security review", cwd="/x/security")
        self.assertTrue(roost.matches(s, "vendor review"))
        self.assertFalse(roost.matches(s, "vendor laptop"))


class Pins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = roost.PINS, roost.JOBS
        roost.JOBS = Path(self.tmp.name)
        roost.PINS = roost.JOBS / "pins.json"

    def tearDown(self):
        roost.PINS, roost.JOBS = self.saved
        self.tmp.cleanup()

    def test_pin_and_unpin_round_trip(self):
        roost.set_pin("abc12345", True)
        roost.set_pin("abc12345", True)  # pinning twice doesn't duplicate
        self.assertEqual(json.loads(roost.PINS.read_text()), ["abc12345"])
        roost.set_pin("abc12345", False)
        self.assertEqual(json.loads(roost.PINS.read_text()), [])
        self.assertFalse(Path(str(roost.PINS) + ".lock").exists())

    def test_stale_lock_is_cleared(self):
        lock = Path(str(roost.PINS) + ".lock")
        lock.mkdir()
        old = time.time() - 60
        import os
        os.utime(lock, (old, old))
        roost.set_pin("abc12345", True)
        self.assertEqual(json.loads(roost.PINS.read_text()), ["abc12345"])


class Rename(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = roost.PROJECTS
        roost.PROJECTS = Path(self.tmp.name)
        self.sid = "1234abcd-0000-0000-0000-000000000000"
        d = roost.PROJECTS / "-proj"; d.mkdir()
        self.path = d / f"{self.sid}.jsonl"
        self.path.write_text(json.dumps({"type": "custom-title", "customTitle": "Old name", "sessionId": self.sid}) + "\n")

    def tearDown(self):
        roost.PROJECTS = self.saved
        self.tmp.cleanup()

    def closed(self):
        s = roost.parse(self.path)
        s.update(state="closed", kind=None, attach=None)
        return s

    def test_closed_rename_is_read_back(self):
        roost.rename_session(self.closed(), "  New   name ")
        self.assertEqual(roost.parse(self.path)["name"], "New name")

    def test_empty_name_refused(self):
        with self.assertRaises(RuntimeError):
            roost.rename_session(self.closed(), "   ")

    def test_open_window_refused(self):
        s = self.closed(); s.update(kind="interactive", state="idle")
        with self.assertRaises(RuntimeError):
            roost.rename_session(s, "x")
        self.assertEqual(roost.parse(self.path)["name"], "Old name")

    def test_busy_background_refused_without_restart(self):
        s = self.closed(); s.update(kind="background", state="busy", attach="1234abcd")
        called = []
        saved = roost.stop_session
        roost.stop_session = lambda job: called.append(job)
        try:
            with self.assertRaises(RuntimeError):
                roost.rename_session(s, "x")
        finally:
            roost.stop_session = saved
        self.assertEqual(called, [])


class Refresh(unittest.TestCase):
    def setUp(self):
        self.saved = (roost.load, roost.live_sessions, roost.read_pins)
        roost.read_pins = lambda: set()

    def tearDown(self):
        roost.load, roost.live_sessions, roost.read_pins = self.saved

    def test_regather_picks_up_new_transcript_data(self):
        a = dict(session("a"), ids=["a-id"], id="a-id", tokens=10, summary="old")
        roost.load = lambda days: [dict(a, tokens=99, summary="new")]
        roost.live_sessions = lambda: {"a-id": {"sessionId": "a-id", "status": "busy", "kind": "background", "id": "a-id"}}
        got = roost.regather([a])
        self.assertEqual((got[0]["tokens"], got[0]["summary"], got[0]["state"]), (99, "new", "busy"))

    def test_regather_keeps_opening_window(self):
        a = dict(session("a"), ids=["a-id"], id="a-id", opening=time.time())
        roost.load = lambda days: [dict(a, opening=None)]
        roost.live_sessions = lambda: {}
        got = roost.regather([a])
        self.assertEqual(got[0]["state"], "opening")

    def test_busy_after_new_turn_is_not_watching(self):
        # transcript now shows a fresh assistant turn (turn_done False): must read as working
        a = dict(session("a", mins=30), ids=["a-id"], id="a-id", turn_done=True)
        roost.load = lambda days: [dict(a, turn_done=False)]
        roost.live_sessions = lambda: {"a-id": {"sessionId": "a-id", "status": "busy", "kind": "background", "id": "a-id"}}
        self.assertEqual(roost.regather([a])[0]["state"], "busy")


class Retention(unittest.TestCase):
    DAY = 86400

    def closed(self, age_days, entrypoint="cli"):
        s = session("x", state="closed", kind=None)
        s.update(last=1_000_000_000 - age_days * self.DAY, entrypoint=entrypoint)
        return s

    def left(self, s, rules=(30, None)):
        return roost.days_left(s, rules, now=1_000_000_000)

    def test_counts_down_from_last_use(self):
        self.assertAlmostEqual(self.left(self.closed(18)), 12)

    def test_running_sessions_have_no_countdown(self):
        self.assertIsNone(self.left(session("x", state="idle")))

    def test_desktop_sessions_are_kept_unless_configured(self):
        self.assertEqual(self.left(self.closed(90, "claude-desktop")), float("inf"))
        self.assertAlmostEqual(self.left(self.closed(5, "claude-desktop"), (30, 7)), 2)

    def test_custom_period(self):
        self.assertAlmostEqual(self.left(self.closed(18), (60, None)), 42)

    def test_overdue_is_negative(self):
        self.assertLess(self.left(self.closed(31)), 0)

    def test_settings_priority_and_default(self):
        with tempfile.TemporaryDirectory() as d:
            managed, user = Path(d) / "managed.json", Path(d) / "user.json"
            saved = roost.RETENTION_SETTINGS
            roost.RETENTION_SETTINGS = [managed, user]
            try:
                self.assertEqual(roost.retention(), (30, None))
                user.write_text(json.dumps({"cleanupPeriodDays": 60}))
                self.assertEqual(roost.retention(), (60, None))
                managed.write_text(json.dumps({"cleanupPeriodDays": 14, "desktopSessionCleanupPeriodDays": 90}))
                self.assertEqual(roost.retention(), (14, 90))
            finally:
                roost.RETENTION_SETTINGS = saved

    def test_parse_reads_entrypoint(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "abc.jsonl"
            p.write_text(json.dumps({"type": "user", "entrypoint": "claude-desktop", "uuid": "u1"}, separators=(",", ":")) + "\n")
            self.assertEqual(roost.parse(p)["entrypoint"], "claude-desktop")

    def test_resumes_in_launch_folder_after_cd(self):
        with tempfile.TemporaryDirectory() as d:
            sub = Path(d) / "projects" / "x"
            sub.mkdir(parents=True)
            p = Path(d) / "abc.jsonl"
            recs = [{"type": "user", "cwd": d, "uuid": "u1"}, {"type": "user", "cwd": str(sub), "uuid": "u2"}]
            p.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in recs))
            s = roost.parse(p)
            self.assertEqual(s["cwd"], str(sub))
            self.assertEqual(roost.launch_dir(s), d)
            self.assertEqual(roost.launch_dir({"cwd": str(sub)}), str(sub))  # older index entries


class Governor(unittest.TestCase):
    NOW = 2_000_000_000

    def sess(self, **kw):
        s = {"tokens": 200_000, "state": "closed", "last": self.NOW - 600, "reply_at": self.NOW - 600, "ttl": 3600}
        s.update(kw)
        return s

    def test_cache_warm_then_cold(self):
        self.assertEqual(roost.cache_left(self.sess(), self.NOW), 3000)
        self.assertAlmostEqual(roost.next_cost(self.sess(), self.NOW), 0.1)
        cold = self.sess(reply_at=self.NOW - 4000)
        self.assertLess(roost.cache_left(cold, self.NOW), 0)
        self.assertEqual(roost.next_cost(cold, self.NOW), 2.0)
        self.assertEqual(roost.next_cost(self.sess(reply_at=self.NOW - 400, ttl=300), self.NOW), 1.25)

    def test_no_context_has_no_cache(self):
        self.assertIsNone(roost.cache_left(self.sess(tokens=0), self.NOW))
        self.assertIsNone(roost.next_cost(self.sess(tokens=0), self.NOW))

    def test_working_session_stays_warm(self):
        self.assertGreater(roost.cache_left(self.sess(state="busy", reply_at=self.NOW - 9999), self.NOW), 0)

    def test_stale_needs_big_idle_and_not_working(self):
        old = self.NOW - 3 * 86400
        self.assertTrue(roost.stale(self.sess(last=old), self.NOW))
        self.assertFalse(roost.stale(self.sess(last=old, tokens=100_000), self.NOW))
        self.assertFalse(roost.stale(self.sess(), self.NOW))
        self.assertFalse(roost.stale(self.sess(last=old, state="busy"), self.NOW))
        self.assertTrue(roost.stale(self.sess(last=old, state="idle"), self.NOW))  # pinned but untouched

    def test_a_session_being_summarized_stays_in_running(self):
        s = self.sess(id="abc", state="closed", last=time.time())  # stopped so its Handoff can be written
        running = roost.VIEWS.index("Running")
        self.assertFalse(roost.in_view(s, running))
        self.assertTrue(roost.in_view(s, running, keep={"abc"}))

    def test_a_session_being_summarized_sits_in_wrapping_up_after_closed(self):
        now = time.time()
        wrapping = self.sess(id="abc", state="closed", kind=None, last=now)  # stopped so its Handoff can be written
        bg = self.sess(id="bg", state="idle", kind="background", last=now - 60)
        closed = self.sess(id="old", state="closed", kind=None, last=now - 120)
        self.assertEqual(roost.SECTIONS[roost.group(wrapping, keep={"abc"})], "Wrapping up")
        self.assertEqual(roost.SECTIONS[roost.group(wrapping)], "Closed")  # once the run ends, it moves to Closed
        rows = roost.visible([wrapping, bg, closed], CLOSED, "", keep={"abc"})
        self.assertEqual([s["id"] for s in rows], ["old"])  # while roost wraps it up it counts as running, not closed
        self.assertEqual([s["id"] for s in roost.visible([wrapping, bg, closed], RUNNING, "", keep={"abc"})], ["bg", "abc"])

    def test_parse_reads_cache_lifetime_and_times(self):
        recs = [{"type": "user", "uuid": "u1", "timestamp": "2026-10-06T10:00:00.000Z"},
                {"type": "assistant", "timestamp": "2026-10-06T10:00:05.000Z",
                 "message": {"usage": {"input_tokens": 5, "cache_read_input_tokens": 1000,
                                       "cache_creation_input_tokens": 50,
                                       "cache_creation": {"ephemeral_1h_input_tokens": 50, "ephemeral_5m_input_tokens": 0}}}}]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "abc.jsonl"
            p.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in recs))
            s = roost.parse(p)
        self.assertEqual(s["ttl"], 3600)
        self.assertEqual(s["reply_at"] - s["started"], 5)
        self.assertEqual(s["tokens"], 1055)

    def test_parse_reads_a_compaction(self):
        reply = {"type": "assistant", "timestamp": "2026-10-06T10:00:05.000Z",
                 "message": {"usage": {"input_tokens": 5, "cache_read_input_tokens": 167_000}}}
        boundary = {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-10-06T11:00:00.000Z",
                    "compactMetadata": {"trigger": "manual", "preTokens": 167_005, "postTokens": 20_760}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "abc.jsonl"
            p.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in [reply, boundary]))
            s = roost.parse(p)
            self.assertEqual(s["tokens"], 20_760)  # its context is the summary now, not what it had
            self.assertTrue(s["compacted"])
            p.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in [reply, boundary, dict(reply, timestamp="2026-10-06T11:01:00.000Z")]))
            self.assertFalse(roost.parse(p)["compacted"])  # a reply since: back to its cache as usual

    def test_active_ignores_file_touches(self):
        s = self.sess(last=self.NOW, active=self.NOW - 3 * 86400)
        self.assertTrue(roost.stale(s, self.NOW))  # opened today, but nobody has written to it in 3 days
        self.assertEqual(roost.ago(s, self.NOW), "3d ago")
        self.assertEqual(roost.ago(self.sess(state="busy"), self.NOW), "now")
        self.assertEqual(roost.ago(self.sess(active=self.NOW - 20), self.NOW), "just now")

    def test_span(self):
        self.assertEqual([roost.span(x) for x in (30, 600, 7200, 3 * 86400)], ["1m", "10m", "2h", "3d"])

    def test_demo_has_cache_data(self):
        self.assertTrue(all(roost.cache_left(s) is not None for s in roost.demo_sessions() if s["tokens"]))


class StartingSessions(unittest.TestCase):
    """#49: brief, recording Sessions in sessions:, starting a Session from a project, and linking."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.saved = roost.CONFIG, roost.DONE, roost.RECAPS, roost.PLUGIN, roost.PROJECTS, roost.osa, roost.claude_settings, roost.NOTES
        roost.CONFIG, roost.DONE, roost.RECAPS, roost.NOTES = base / "config.json", base / "_done", base / "sessions", base / "notes"
        roost.claude_settings = lambda: {}  # not the person's real Claude Code settings
        roost.PLUGIN, roost.PROJECTS = base / "plugin", base / "transcripts"
        (roost.PLUGIN / ".claude-plugin").mkdir(parents=True)
        (roost.PLUGIN / ".claude-plugin" / "plugin.json").write_text("{}")
        self.root = base / "projects"
        self.root.mkdir()
        roost.save_config(projects_dir=str(self.root))
        self.scripts = []
        roost.osa = lambda script, *args: self.scripts.append(args) or "ok"

    def tearDown(self):
        roost.CONFIG, roost.DONE, roost.RECAPS, roost.PLUGIN, roost.PROJECTS, roost.osa, roost.claude_settings, roost.NOTES = self.saved
        self.tmp.cleanup()

    def project(self, name, text=README, handoffs=()):
        folder = self.root / name
        (folder / "handoffs").mkdir(parents=True)
        (folder / "README.md").write_text(text)
        for h in handoffs:
            (folder / "handoffs" / h).write_text(f"Handoff {h}")
        return folder

    def ui(self, term="ghostty"):
        ui = Projects.ui(self)
        ui.term, ui.tab, ui.helping = term, 0, False
        ui.sel, ui.query = 0, ""
        ui.refresh = ui.load_projects
        return ui

    # roost project brief

    def test_brief_prints_the_readme_and_the_newest_three_handoffs(self):
        names = [f"2026-10-0{d}-0900-aaaaaaaa.md" for d in range(1, 6)] + ["notes.md"]
        text = roost.brief(self.project("p", handoffs=names))
        self.assertIn("summary: \"Two password managers", text)
        self.assertIn("# Newest Handoffs (3 of 5, newest first)", text)
        shown = [n for n in names if f"## handoffs/{n}" in text]
        self.assertEqual(shown, names[2:5])
        self.assertLess(text.index("2026-10-05"), text.index("2026-10-03"))
        self.assertIn("2 older Handoffs are in", text)

    def test_brief_with_no_handoffs_and_for_a_note(self):
        self.assertIn("# No Handoffs yet", roost.brief(self.project("p")))
        roost.NOTES.mkdir()
        (roost.NOTES / "fix.md").write_text("---\ntags: [recap]\n---\n\nRestarted two Macs.")
        self.assertIn("Restarted two Macs.", roost.brief(roost.find_project("fix")))
        (roost.NOTES / "idea.md").write_text("---\ntags: [devices]\n---\n\nAn idea.")
        with self.assertRaises(ValueError):  # only a recap is found by its name; a note by its path
            roost.find_project("idea")
        with self.assertRaises(ValueError):
            roost.find_project("nope")
        with self.assertRaises(ValueError):
            roost.find_project("../p")

    def test_brief_command_records_the_session_once(self):
        folder = self.project("p")
        argv = ["brief", "p", "--session", "cc3d0000-1111"]
        self.assertEqual(roost.run_command("project", argv), 0)
        self.assertEqual(roost.run_command("project", argv), 0)
        meta = roost.frontmatter((folder / "README.md").read_text())
        self.assertEqual([x["id"] for x in meta["sessions"]], ["aa1b9a71-0000", "bb2c0000-0000", "cc3d0000-1111"])
        self.assertEqual(meta["sessions"][-1]["name"], "cc3d0000")  # no transcript yet: its short id

    def test_list_command(self):
        self.project("p")
        self.assertEqual(roost.run_command("project", ["list"]), 0)

    # sessions: list

    def test_link_session_changes_only_the_sessions_lines(self):
        before = README
        folder = self.project("p")
        self.assertTrue(roost.link_session(folder, "cc3d0000-1111", "Agent Roost"))
        after = (folder / "README.md").read_text()
        self.assertEqual(after.replace("  - name: Agent Roost\n    id: cc3d0000-1111\n", ""), before)
        self.assertFalse(roost.link_session(folder, "cc3d0000-1111", "Agent Roost"))

    def test_link_session_into_an_empty_or_missing_list(self):
        folder = self.project("a", "---\nsummary: s\nsessions: []\nstatus: active\n---\n\n# a\n")
        roost.link_session(folder, "id-1", "Odd: name # here")
        self.assertEqual((folder / "README.md").read_text(),
                         '---\nsummary: s\nsessions:\n  - name: "Odd: name # here"\n    id: id-1\nstatus: active\n---\n\n# a\n')
        self.assertEqual(roost.frontmatter((folder / "README.md").read_text())["sessions"][0]["name"], "Odd: name # here")
        folder = self.project("b", "---\nsummary: s\n---\nbody\n")
        roost.link_session(folder, "id-2", "Two")
        self.assertEqual((folder / "README.md").read_text(), "---\nsummary: s\nsessions:\n  - name: Two\n    id: id-2\n---\nbody\n")

    def test_link_session_refuses_a_flow_list_or_no_frontmatter(self):
        with self.assertRaises(ValueError):
            roost.link_session(self.project("a", "---\nsessions: [{id: x}]\n---\n"), "y", "Y")
        with self.assertRaises(ValueError):
            roost.link_session(self.project("b", "# no frontmatter\n"), "y", "Y")

    def test_unlink_and_assign_move_a_session_between_projects(self):
        a, b = self.project("a"), self.project("b", "---\nsummary: s\nsessions: []\n---\n")
        self.assertEqual(roost.assign_session("bb2c0000-0000", "Old", b), (True, ["a"]))
        self.assertEqual([x["id"] for x in roost.frontmatter((a / "README.md").read_text())["sessions"]], ["aa1b9a71-0000"])
        roost.unlink_session(b, "bb2c0000-0000")
        self.assertIn("sessions: []\n", (b / "README.md").read_text())
        self.assertFalse(roost.unlink_session(b, "bb2c0000-0000"))

    # starting a Session

    def test_start_opens_a_window_with_pickup_then_records_it(self):
        folder = self.project("p")
        msg = roost.start_session("p", str(folder), "ghostty")
        self.assertTrue(msg.startswith("Started a Session for p"))
        cwd, typed, direct = self.scripts[0]
        self.assertEqual(cwd, str(folder))
        self.assertIn("/roost:pickup p", direct)
        self.assertIn(f"--plugin-dir {roost.PLUGIN}", direct)
        self.assertNotIn("--resume", direct)
        sid = re.search(r"--session-id ([0-9a-f-]{36})", direct).group(1)
        meta = roost.frontmatter((folder / "README.md").read_text())
        self.assertEqual(meta["sessions"][-1], {"name": "p", "id": sid})

    def test_a_failed_window_records_nothing(self):
        folder = self.project("p")
        roost.osa = lambda *a: None
        self.assertIn("Couldn't open", roost.start_session("p", str(folder), "ghostty"))
        self.assertEqual(len(roost.frontmatter((folder / "README.md").read_text())["sessions"]), 2)

    def test_without_a_terminal_it_hands_back_what_to_run(self):
        folder = self.project("p")
        run = roost.start_session("p", str(folder), None)
        self.assertEqual((run["cwd"], run["start"][0], run["start"][-1]), (str(folder), "claude", "/roost:pickup p"))
        self.assertEqual(len(roost.frontmatter((folder / "README.md").read_text())["sessions"]), 3)

    # In an IDE's terminal, what would run here is copied instead, to paste in a new tab

    JETBRAINS_NOTE = "Copied the command: press ⌘T and paste it to open it in a new tab"
    VSCODE_NOTE = "Copied the command: open a new terminal (⌃⇧`) and paste it"

    def in_ide(self, ide, clipboard=None):
        """Patches for a test in an IDE's terminal (None: in no IDE), with the clipboard captured in
        self.copied, or clipboard in its place."""
        import unittest.mock
        self.copied = []
        return (unittest.mock.patch.object(roost.terminals, "ide_terminal", lambda env=None: ide),
                unittest.mock.patch.object(roost.UI, "clipboard", staticmethod(clipboard or self.copied.append)))

    def open_here(self, ui):
        """Enter on the Sessions tab's only Session, then enter on Open here, through dispatch."""
        ui.tab, ui.view, ui.top, ui.quit_anyway = [t.name for t in ui.TABS].index("Sessions"), 0, 0, False
        ui.focus = "pane"  # the Sessions view focused, so enter goes to it
        ui.dispatch("enter", ui.sessions)
        opts = ui.choosing["options"]
        ui.choosing["sel"] = next(i for i, (l, _) in enumerate(opts) if l == "Open here")
        return ui.dispatch("enter", None)

    def test_in_jetbrains_open_here_copies_the_attach_line_and_keeps_roost_running(self):
        ui = self.ui(term=None)
        ui.sessions = [dict(session("Live"), attach="abcd1234")]
        ide, clip = self.in_ide("jetbrains")
        with ide, clip:
            self.assertIsNone(self.open_here(ui))
        self.assertEqual((self.copied, ui.msg), (["claude attach abcd1234"], self.JETBRAINS_NOTE))

    def test_in_vscode_start_a_session_copies_the_line_that_starts_it(self):
        self.project("p", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        repo = Path(self.tmp.name) / "my repo"  # a space: the folder comes out quoted
        repo.mkdir()
        ui = self.ui(term=None)
        ui.quit_anyway, ui.psel = False, 0  # the project: no header above it
        ui.sessions = [dict(session("Mine", cwd=str(repo)), id="aa1b9a71-0000", home=str(repo))]
        ui.load_projects()
        ide, clip = self.in_ide("vscode")
        with ide, clip:
            ui.dispatch("enter", ui.project_rows())
            opts = ui.choosing["options"]
            ui.choosing["sel"] = next(i for i, (l, _) in enumerate(opts) if l.startswith("Start a Session"))
            ui.dispatch("enter", None)  # which folder: the project's, then the Session's
            ui.dispatch("down", None)
            self.assertIsNone(ui.dispatch("enter", None))
        self.assertEqual(ui.msg, self.VSCODE_NOTE)
        self.assertEqual(len(self.copied), 1)
        self.assertTrue(self.copied[0].startswith(f"cd '{repo}' && claude --session-id "), self.copied[0])
        self.assertTrue(self.copied[0].endswith(" '/roost:pickup p'"), self.copied[0])

    def test_in_an_ide_ctrl_n_new_session_copies_the_line_that_starts_it(self):
        import unittest.mock
        folder = Path(self.tmp.name) / "a repo"
        folder.mkdir()
        ui = self.ui(term=None)
        ui.quit_anyway = False
        ide, clip = self.in_ide("jetbrains")
        with ide, clip, unittest.mock.patch.object(roost, "plugin_args", lambda: []):
            ui.dispatch("ctrl+n", None)
            ui.dispatch("s", None)
            ui.asking["text"] = str(folder)
            self.assertIsNone(ui.dispatch("enter", None))
        self.assertEqual((self.copied, ui.msg), ([f"cd '{os.path.realpath(folder)}' && claude"], self.JETBRAINS_NOTE))

    def test_in_an_ide_a_failed_copy_says_so_and_keeps_roost_running(self):
        def broken(text):
            raise OSError("pbcopy is missing")
        ui = self.ui(term=None)
        ui.sessions = [dict(session("Live"), attach="abcd1234")]
        ide, clip = self.in_ide("vscode", clipboard=broken)
        with ide, clip:
            self.assertIsNone(self.open_here(ui))
        self.assertTrue(ui.msg.startswith("Couldn't copy"), ui.msg)

    def test_in_no_ide_open_here_hands_back_the_session_to_run_here(self):
        ui = self.ui(term=None)
        ui.sessions = [dict(session("Live"), attach="abcd1234")]
        ide, clip = self.in_ide(None)
        with ide, clip:
            self.assertIs(self.open_here(ui), ui.sessions[0])
        self.assertEqual(self.copied, [])

    def test_no_plugin_means_no_start(self):
        (roost.PLUGIN / ".claude-plugin" / "plugin.json").unlink()
        with self.assertRaises(ValueError):
            roost.start_session("p", str(self.project("p")), "ghostty")
        self.assertEqual(self.scripts, [])

    def test_s_starts_in_the_only_folder_or_asks_which(self):
        folder = self.project("p", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        ui = self.ui()
        ui.psel = 0  # the project: no header above it
        act(ui, ui.keys_projects, ui.project_rows(), "Start a Session")
        self.assertEqual((len(self.scripts), ui.choosing), (1, None))
        repo = Path(self.tmp.name) / "repo"
        repo.mkdir()
        ui.sessions = [dict(session("Mine", cwd=str(repo)), id="aa1b9a71-0000", home=str(repo))]
        ui.load_projects()
        act(ui, ui.keys_projects, ui.project_rows(), "Start a Session")
        self.assertEqual([v for _, v in ui.choosing["options"]], [str(folder), str(repo)])
        ui.dispatch("down", None)
        ui.dispatch("enter", None)
        self.assertEqual((self.scripts[1][0], ui.choosing), (str(repo), None))

    def test_s_on_a_closed_project_starts_nothing(self):
        self.project("p", README.replace("status: active", "status: closed"))
        ui = self.ui()
        ui.pquery = "title"  # closed: only a search lists it in the sidebar
        rows = ui.project_rows()
        ui.psel = next(i for i, r in enumerate(rows) if r.kind == "project")
        self.assertNotIn("Start a Session", labels(ui, ui.keys_projects, rows))
        ui.start_for(rows[ui.psel].item)
        self.assertEqual((self.scripts, ui.msg), ([], "Title is closed"))

    def test_enter_on_a_closed_session_never_resumes(self):
        self.project("p", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        ui = self.ui()
        opened = []
        saved, roost.open_in_window = roost.open_in_window, lambda s, term: opened.append(s) or "opened"
        try:
            ui.sessions = [dict(session("Old", state="closed", kind=None), id="bb2c0000-0000")]
            ui.load_projects()
            acts = labels(ui, ui.keys_sessions, ui.sessions)  # on the Sessions tab: the Projects tab lists no Sessions
            self.assertEqual(acts[0], "Write its Handoff from the transcript")  # no Handoff covers it: saving it comes first
            self.assertIn("Start a fresh Session for Title", acts)  # by its README's heading
            self.assertFalse([a for a in acts if a.startswith(("Open", "Go to"))])
            # opening it directly is refused
            self.assertIsNone(ui.open_one(ui.sessions[0]))
            self.assertIn("fresh Session", ui.msg)
            self.assertEqual(opened, [])
        finally:
            roost.open_in_window = saved

    def test_a_session_open_in_claude_desktop_says_so_and_enter_goes_there(self):
        """Claude Desktop runs its sessions with its own copy of claude, which `claude agents` lists as
        interactive like a terminal's: roost tags it Desktop and Enter switches to the Desktop app."""
        import subprocess, unittest.mock
        ps = ("47108 /Users/x/Library/Application Support/Claude/claude-code/2.1.295/abc/claude.app/Contents/MacOS/claude --output-format stream-json\n"
              " 5720 claude\n")
        with unittest.mock.patch.object(roost.subprocess, "run",
                                        lambda *a, **kw: subprocess.CompletedProcess(a, 0, ps, "")):
            self.assertEqual(roost.desktop_pids([47108, 5720]), {47108})
        live = {"d-id": {"sessionId": "d-id", "kind": "interactive", "status": "idle", "pid": 47108, "desktop": True}}
        s = dict(session("Budget", state="closed", kind=None), id="d-id", ids=["d-id"])
        roost.attach_live([s], live)
        self.assertTrue(s["desktop"])
        ui = self.ui()
        ui.DIM, ui.status = 0, {}
        ui.sessions = [s]
        self.assertEqual(ui.status_cell(s)[0], "○ open in Desktop")
        self.assertEqual(ui.session_actions(s)[0][0], "Go to Claude Desktop")
        scripts = []
        with unittest.mock.patch.object(roost, "osa", lambda script, *a: scripts.append(script) or ""):
            self.assertEqual(roost.open_in_window(s, "ghostty"), "Switched to Claude Desktop, where Budget is open")
            ui.term = None  # no terminal set: still never resumed here, where Desktop already has it
            self.assertIsNone(ui.open_one(s))
        self.assertEqual(scripts, ['tell application "Claude" to activate'] * 2)

    # minimizing in the Herdr app: offered for a Session open in a window, and Restore once it's minimized

    def minimize_actions(self, s, term="herdr-app", recorded=None):
        """The labels of s's actions with terminal term, config.MINIMIZED holding recorded ({} if None)."""
        import unittest.mock
        minimized = Path(self.tmp.name) / "minimized.json"
        minimized.write_text(json.dumps(recorded or {}))
        with unittest.mock.patch.object(roost.config, "MINIMIZED", minimized):
            ui = self.ui(term=term)
            ui.sessions = [s]
            return [a for a, _ in ui.session_actions(s)]

    def test_herdr_app_offers_minimize_for_a_session_open_in_a_window(self):
        for s in (dict(session("Live", kind="interactive"), id="aa1b9a71-0000"),
                  dict(session("Kept", attached=True), id="bb1b9a71-0000")):
            with self.subTest(s["name"]):
                acts = self.minimize_actions(s)
                self.assertIn("Minimize its window", acts)
                self.assertNotIn("Restore its window", acts)

    def test_choosing_minimize_minimizes_that_session(self):
        import unittest.mock
        s = dict(session("Live", kind="interactive"), id="aa1b9a71-0000")
        minimized = Path(self.tmp.name) / "minimized.json"
        parked = []
        with unittest.mock.patch.object(roost.config, "MINIMIZED", minimized), \
                unittest.mock.patch.object(roost.terminals, "app_minimize", lambda x: parked.append(x["id"]) or True):
            ui = self.ui(term="herdr-app")
            ui.sessions = [s]
            dict(ui.session_actions(s))["Minimize its window"]()
        self.assertEqual(parked, [s["id"]])

    def test_a_minimized_session_offers_restore_instead_of_minimize(self):
        s = dict(session("Live", kind="interactive"), id="aa1b9a71-0000")
        acts = self.minimize_actions(s, recorded={s["id"]: {"terminal": "t7", "space": "p", "tab": "Live", "folder": "/x"}})
        self.assertEqual(acts[0], "Restore its window")
        self.assertNotIn("Minimize its window", acts)
        self.assertNotIn("Go to its window", acts)

    def test_minimize_is_only_offered_in_the_herdr_app(self):
        s = dict(session("Live", kind="interactive"), id="aa1b9a71-0000")
        for term in ("ghostty", "herdr", None):
            with self.subTest(term):
                self.assertNotIn("Minimize its window", self.minimize_actions(s, term=term))

    def test_minimize_is_not_offered_for_a_session_not_in_a_window(self):
        self.assertNotIn("Minimize its window", self.minimize_actions(dict(session("Bg"), id="cc1b9a71-0000")))

    def test_minimize_is_not_offered_for_a_claude_desktop_session(self):
        s = dict(session("Budget", kind="interactive"), id="d-id", desktop=True)
        self.assertNotIn("Minimize its window", self.minimize_actions(s))

    def test_stop_and_delete_are_actions_that_ask_first(self):
        self.project("p", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        ui = self.ui()
        did = []
        saved = roost.stop_session, roost.delete_session
        roost.stop_session, roost.delete_session = (lambda job: did.append(("stop", job))), (lambda s: did.append(("delete", s["name"])))
        try:
            ui.sessions = [dict(session("Live"), id="aa1b9a71-0000"), dict(session("Old", state="closed", kind=None, mins=60), id="bb2c0000-0000")]
            ui.load_projects()
            rows = ui.project_rows()
            ui.psel = 0  # the project: no header above it
            self.assertFalse([a for a in labels(ui, ui.keys_projects, rows)  # no Session Stop or Delete: the project's own Delete is allowed
                              if a.startswith(("Stop", "Delete")) and a != "Delete"])
            live, old = 0, 1  # on the Sessions tab: the Projects tab lists no Sessions
            ui.sel = live
            act(ui, ui.keys_sessions, ui.sessions, "Stop")
            self.assertTrue(ui.sessions_typing())
            ui.keys_confirm("n")  # any other key cancels
            self.assertEqual((did, ui.msg), ([], "Cancelled"))
            act(ui, ui.keys_sessions, ui.sessions, "Stop")
            ui.keys_confirm("y")
            ui.sel = old
            act(ui, ui.keys_sessions, ui.sessions, "Delete to the Trash")
            ui.keys_confirm("y")
            self.assertEqual(did, [("stop", ui.sessions[0]["attach"]), ("delete", "Old")])
        finally:
            roost.stop_session, roost.delete_session = saved

    def test_add_to_a_project_links_a_session_with_a_filtered_picker(self):
        self.project("alpha", "---\nsummary: s\nsessions: []\n---\n")
        b = self.project("beta", "---\nsummary: s\nsessions: []\n---\n")
        ui = self.ui()
        ui.sessions = [dict(session("Loose"), id="dd4e0000-0000")]
        act(ui, ui.keys_sessions, ui.sessions, "Add to a project")
        for k in names("bet\n"):
            ui.dispatch(k, None)
        self.assertEqual(ui.msg, "Added Loose to beta")
        self.assertEqual(roost.frontmatter((b / "README.md").read_text())["sessions"], [{"name": "Loose", "id": "dd4e0000-0000"}])
        act(ui, ui.keys_sessions, ui.sessions, "Move to another project")
        self.assertEqual([label for label, _ in ui.choosing["options"]], ["alpha"])
        for k in names("alp\n"):
            ui.dispatch(k, None)
        self.assertEqual(ui.msg, "Moved Loose from beta to alpha")

    def test_new_project_from_a_session_lists_it_and_offers_its_handoff(self):
        ui = self.ui()
        offered = []
        ui.ask_summary = offered.append
        ui.sessions = [dict(session("Printer Fix!"), id="dd4e0000-0000")]
        act(ui, ui.keys_sessions, ui.sessions, "New project from this Session")
        self.assertEqual(ui.asking["text"], "Printer Fix!")  # its name starts as the Session's
        typed(ui, "\n")
        self.assertEqual(ui.asking["text"], "printer-fix")  # then its folder, from the name
        typed(ui, "\n")
        self.assertEqual(ui.asking["text"], "Printer Fix!")  # then the summary
        typed(ui, "\x15Fix the printers.\n")
        meta = roost.frontmatter((self.root / "printer-fix" / "README.md").read_text())
        self.assertEqual((meta["description"], meta["sessions"]), ("Fix the printers.", [{"name": "Printer Fix!", "id": "dd4e0000-0000"}]))
        self.assertEqual(offered, ui.sessions)  # its first Handoff, after the cost is shown
        self.assertNotIn("New project from this Session", labels(ui, ui.keys_sessions, ui.sessions))
        # a Session open in a window writes its own Handoff with /wrap-up
        ui.sessions = [dict(session("Other", kind="interactive"), id="ee5f0000-0000")]
        act(ui, ui.keys_sessions, ui.sessions, "New project from this Session")
        typed(ui, "\n\n\n")
        self.assertIn("/wrap-up", ui.msg)
        self.assertEqual(len(offered), 1)

    def test_letters_do_nothing_on_either_tab(self):
        self.project("p", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        ui = self.ui()
        ui.sessions = [dict(session("Live"), id="aa1b9a71-0000"), session("Loose")]
        ui.load_projects()
        ui.psel, ui.view, ui.top = 1, 0, 0
        for k in "apnwyoXtcsrjkghl[]12\x18\x04":
            ui.keys_sessions(k, ui.sessions)
            ui.keys_projects(k, ui.project_rows())
        self.assertEqual((ui.choosing, ui.asking, ui.confirm, ui.msg, self.scripts), (None, None, None, "", []))
        self.assertEqual((ui.view, ui.expanded), (0, set()))

    def test_right_click_opens_the_actions_and_double_click_runs_the_first(self):
        ui = self.ui()
        opened = []
        saved, roost.open_in_window = roost.open_in_window, lambda s, term: opened.append(s["name"]) or "opened"
        try:
            ui.sessions, ui.row_y, ui.tabs, ui.col_x, ui.list_top = [session("Loose")], {6: 0}, [], [], 4
            ui.keys_sessions(("mouse", 10, 6, roost.curses.BUTTON3_PRESSED), ui.sessions)
            self.assertEqual(ui.choosing["title"], "Loose")
            ui.choosing = None
            ui.keys_sessions(("mouse", 10, 6, roost.curses.BUTTON1_DOUBLE_CLICKED), ui.sessions)
            self.assertEqual((opened, ui.choosing), (["Loose"], None))
        finally:
            roost.open_in_window = saved

    def test_the_wrap_up_skill_pipes_its_summary_to_roost_handoff(self):
        skill = (ROOT / "plugin" / "skills" / "wrap-up" / "SKILL.md").read_text()
        meta = roost.frontmatter(skill)
        self.assertEqual(meta["name"], "wrap-up")
        self.assertIn("Use when the person runs /wrap-up", meta["description"])
        self.assertIn("roost handoff --session ${CLAUDE_SESSION_ID} <<'ROOST_SUMMARY'", skill)
        for heading in ["What I did", "What worked", "What failed and why", "Decisions", "Open questions", "Next"]:
            self.assertIn(f"### {heading}", skill)
        self.assertIn("roost project status <id> <value>", skill)
        self.assertIn("roost project new <id>", skill)
        self.assertIn("Safe to /clear or exit.", skill)
        for cmd in ["handoff", "project"]:  # every command the skill names exists
            self.assertIn(cmd, roost.COMMANDS)

    def test_the_pickup_skill_runs_brief_with_this_sessions_id(self):
        skill = (ROOT / "plugin" / "skills" / "pickup" / "SKILL.md").read_text()
        meta = roost.frontmatter(skill)
        self.assertEqual(meta["name"], "pickup")
        self.assertIn("roost project brief", skill)
        self.assertIn("--session ${CLAUDE_SESSION_ID}", skill)
        self.assertIn("roost project list", skill)
        self.assertEqual(json.loads((ROOT / "plugin" / ".claude-plugin" / "plugin.json").read_text())["name"], "roost")


class SubProjectSessions(unittest.TestCase):
    """A Session listed in a sub-project belongs to it: its project, its menu, project: search, and
    Add to a project / Move to another project."""
    setUp, tearDown, project, ui = StartingSessions.setUp, StartingSessions.tearDown, StartingSessions.project, StartingSessions.ui
    sub = SubProjects.sub

    def fixture(self):
        """Project rollout (listing aa1b9a71-0000) with sub-project alpha (listing cc3d0000-1111), done gamma;
        project other. Sessions: the sub-project's, the parent's, and a loose one."""
        self.project("rollout", "---\nsummary: Rollout work.\nstatus: active\nsessions:\n  - name: Parent one\n"
                                "    id: aa1b9a71-0000\n---\n\n# Rollout\n")
        self.project("other", "---\nsummary: Other work.\nstatus: active\nsessions: []\n---\n")
        self.alpha = self.sub("rollout", "alpha", "feature", "active", "Badge printers.", [("Sub one", "cc3d0000-1111")])
        self.sub("rollout", "gamma", "research", "done", "Old survey.")
        ui = self.ui()
        ui.sessions = [dict(session("Sub one"), id="cc3d0000-1111"), dict(session("Parent one"), id="aa1b9a71-0000"),
                       dict(session("Loose"), id="dd4e0000-0000")]
        ui.load_projects()
        return ui

    def test_a_session_listed_in_a_sub_project_belongs_to_it(self):
        ui = self.fixture()
        p = ui.project_of(ui.sessions[0])
        self.assertIsNotNone(p)
        self.assertEqual(p["id"], "rollout/alpha")
        self.assertEqual(ui.project_of(ui.sessions[1])["id"], "rollout")
        ui.sel = 0
        acts = labels(ui, ui.keys_sessions, ui.sessions)
        self.assertTrue([l for l in acts if l.startswith("Write its Handoff from the transcript")], acts)
        self.assertFalse([l for l in acts if l.startswith("Write its Session recap")], acts)

    def test_project_search_matches_a_sub_projects_sessions_by_its_id_or_its_parents(self):
        ui = self.fixture()
        ui.view, ui.sort, ui.summaries, ui.update_note, ui.warning = RUNNING, ("LAST ACTIVE", True), {}, "", ""  # the fixture's are all running
        ui.DIM = ui.GREEN = ui.YELLOW = ui.RED = ui.CYAN = ui.ACCENT = 0
        ui.scr = type("Screen", (), {"getmaxyx": lambda self: (40, 120)})()  # draw_sessions only asks its size
        for q, want in [("project:rollout/alpha", {"cc3d0000-1111"}), ("project:rollout", {"cc3d0000-1111", "aa1b9a71-0000"})]:
            ui.query = q
            ui.draw_sessions()
            self.assertEqual({s["id"] for s in ui.srows}, want, q)

    def test_add_to_a_project_offers_open_sub_projects_and_lists_the_session_in_one(self):
        ui = self.fixture()
        ui.sel = 2  # Loose
        act(ui, ui.keys_sessions, ui.sessions, "Add to a project")
        opts = [l for l, _ in ui.choice_options()]
        for pid in ("rollout", "other", "rollout/alpha"):  # rollout is labeled by its heading, Rollout
            self.assertTrue([l for l in opts if pid in l.lower()], (pid, opts))
        self.assertFalse([l for l in opts if "gamma" in l], opts)  # done: not offered
        ui.choosing["sel"] = next(i for i, l in enumerate(opts) if "rollout/alpha" in l)
        ui.keys_choice("enter")
        listed = [x["id"] for x in roost.frontmatter((self.alpha / "README.md").read_text())["sessions"]]
        self.assertEqual(listed, ["cc3d0000-1111", "dd4e0000-0000"])

    def test_move_to_another_project_offers_open_sub_projects(self):
        ui = self.fixture()
        ui.sel = 1  # Parent one, in rollout
        act(ui, ui.keys_sessions, ui.sessions, "Move to another project")
        opts = [l for l, _ in ui.choice_options()]
        self.assertTrue([l for l in opts if "rollout/alpha" in l], opts)
        ui.choosing["sel"] = next(i for i, l in enumerate(opts) if "rollout/alpha" in l)
        ui.keys_choice("enter")
        listed = [x["id"] for x in roost.frontmatter((self.alpha / "README.md").read_text())["sessions"]]
        self.assertEqual(listed, ["cc3d0000-1111", "aa1b9a71-0000"])
        self.assertEqual(roost.frontmatter((self.root / "rollout" / "README.md").read_text())["sessions"], [])


class Dismissing(unittest.TestCase):
    """Spec 4: Dismiss and Undismiss on a closed Session, from the Sessions tab's actions. Temp
    folders only: config.DISMISSED, the projects, and the transcripts are all in a temp folder."""
    project, ui = StartingSessions.project, StartingSessions.ui

    def setUp(self):
        StartingSessions.setUp(self)
        self.addCleanup(StartingSessions.tearDown, self)
        patcher, self.persisted = dismissed_file(Path(self.tmp.name) / "dismissed.json")
        self.addCleanup(patcher.stop)

    def fixture(self):
        """Project p (its README lists aa1b9a71-0000 and bb2c0000-0000), a transcript for the closed
        one, and Sessions: Live (idle, in p), Asking (waiting), Old (closed, in p), Loose (closed, no project)."""
        self.folder = self.project("p", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        self.transcript = roost.PROJECTS / "-tmp" / "bb2c0000-0000.jsonl"
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_text('{"type": "user", "message": {"content": "hi"}}\n')
        self.sessions = [dict(session("Live"), id="aa1b9a71-0000"), dict(session("Asking", state="waiting"), id="cc3d0000-0000"),
                         dict(session("Old", state="closed", kind=None, mins=60), id="bb2c0000-0000"),
                         dict(session("Loose", state="closed", kind=None, mins=30), id="dd4e0000-0000")]
        return self.fresh()

    def fresh(self):
        """A new UI reading the same state: the same Sessions, projects, and config.DISMISSED."""
        ui = self.ui()
        ui.DIM, ui.status = 0, {}
        ui.sessions = [dict(s) for s in self.sessions]
        ui.load_projects()
        return ui

    def acts(self, ui, name):
        ui.sel = [s["name"] for s in ui.sessions].index(name)
        return labels(ui, ui.keys_sessions, ui.sessions)

    def pick(self, ui, name, label):
        self.assertIn(label, self.acts(ui, name), name)
        act(ui, ui.keys_sessions, ui.sessions, label)

    def status(self, ui, name):
        return ui.status_cell(next(s for s in ui.sessions if s["name"] == name))[0]

    def test_only_a_closed_session_offers_dismiss(self):
        ui = self.fixture()
        for name in ("Old", "Loose"):  # closed: in a project, and in none
            self.assertIn("Dismiss", self.acts(ui, name), name)
        busy = dict(session("Busy", state="busy"), id="ee5f0000-0000")
        ui.sessions.append(busy)
        for name in ("Live", "Asking", "Busy"):  # idle, needs input, working
            acts = self.acts(ui, name)
            self.assertFalse([l for l in acts if l.startswith(("Dismiss", "Undismiss"))], name)

    def test_dismissing_marks_it_and_persists_without_touching_its_files(self):
        ui = self.fixture()
        readme, transcript = (self.folder / "README.md").read_bytes(), self.transcript.read_bytes()
        stamps = (self.folder / "README.md").stat().st_mtime_ns, self.transcript.stat().st_mtime_ns
        self.assertNotIn("dismissed", self.status(ui, "Old"))
        self.pick(ui, "Old", "Dismiss")
        self.assertIn("dismissed", self.status(ui, "Old"))
        self.assertNotIn("dismissed", self.status(ui, "Loose"))
        self.assertIn("bb2c0000-0000", self.persisted())
        self.assertNotIn("dd4e0000-0000", self.persisted())
        again = self.fresh()  # a new UI reading the same state
        self.assertIn("dismissed", self.status(again, "Old"))
        acts = self.acts(again, "Old")
        self.assertIn("Undismiss", acts)
        self.assertNotIn("Dismiss", acts)
        self.assertEqual((self.folder / "README.md").read_bytes(), readme)  # still in its project's sessions:
        self.assertIn("bb2c0000-0000", roost.read_project(self.folder)["sessions"])
        self.assertEqual(self.transcript.read_bytes(), transcript)
        self.assertEqual(((self.folder / "README.md").stat().st_mtime_ns, self.transcript.stat().st_mtime_ns), stamps)

    def test_undismiss_takes_the_mark_and_the_saved_id_away(self):
        ui = self.fixture()
        self.pick(ui, "Old", "Dismiss")
        self.pick(ui, "Loose", "Dismiss")
        self.pick(ui, "Old", "Undismiss")
        self.assertNotIn("dismissed", self.status(ui, "Old"))
        self.assertIn("dismissed", self.status(ui, "Loose"))
        self.assertNotIn("bb2c0000-0000", self.persisted())
        self.assertIn("dd4e0000-0000", self.persisted())
        again = self.fresh()
        self.assertNotIn("dismissed", self.status(again, "Old"))
        acts = self.acts(again, "Old")
        self.assertIn("Dismiss", acts)
        self.assertNotIn("Undismiss", acts)


class SessionMenuGroups(unittest.TestCase):
    """A Session's actions, in groups split by dividers (| here): work on it, organize it, then stop or delete it."""
    project, ui, fresh, acts = StartingSessions.project, StartingSessions.ui, Dismissing.fresh, Dismissing.acts
    setUp = Dismissing.setUp

    def fixture(self):
        """Dismissing's: project p, Live (idle in the background, in p), and Loose (closed, no project);
        plus project other, so each can go to a project."""
        Dismissing.fixture(self)
        self.project("other", "---\nsummary: Other work.\nstatus: active\nsessions: []\n---\n")
        return self.fresh()

    def test_a_live_background_sessions_actions_in_groups(self):
        ui = self.fixture()
        self.assertEqual(self.acts(ui, "Live"),
                         ["Open in a new window", "Write its Handoff from the transcript", "Ask what to do with it",
                          "Pin: keep it running when idle", DIV,
                          "Move to another project", "Rename", "Copy its id", "Show its folder in Finder", DIV,
                          "Stop"])

    def test_a_closed_sessions_actions_in_groups(self):
        ui = self.fixture()
        self.assertEqual(self.acts(ui, "Loose"),
                         ["Open in a new window", "Write its Session recap from the transcript", "Ask what to do with it",
                          "Restart in the background, pinned", DIV,
                          "New project from this Session", "Add to a project", "Rename", "Dismiss", "Copy its id",
                          "Show its folder in Finder", DIV,
                          "Delete to the Trash"])


    def test_no_action_label_has_an_ellipsis(self):
        """A menu label is just its action's name: none ends in, or holds, "…". Rows: a live background
        Session in a project, and a closed Session in no project."""
        ui = self.fixture()
        for name in ("Live", "Loose"):
            real = [a for a in self.acts(ui, name) if a != DIV]
            self.assertTrue(real, name)
            self.assertEqual([a for a in real if "…" in a], [], name)

class ClosedProjectSessionActions(unittest.TestCase):
    """A closed Session an open project lists and no Handoff covers: its actions start with writing its
    Handoff, then resuming it (with what re-reading its context costs), then a fresh Session for the project.
    Resume reopens it the way open_one does; a plain open stays refused. A wrapped-up one gets no Resume."""
    project, ui, fresh, acts = StartingSessions.project, StartingSessions.ui, Dismissing.fresh, Dismissing.acts
    setUp = Dismissing.setUp
    RESUME = "Resume this Session"

    def fixture(self, **old):
        """Project p (Title) listing Wrapped (aa1b9a71-0000, closed two hours ago, a Handoff covers it to its
        last message) and Old (bb2c0000-0000, closed two hours ago, 412,000 tokens, no Handoff covers it);
        old's keys go into Old."""
        from datetime import datetime, timezone
        folder = self.project("p", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        wrapped = dict(session("Wrapped", state="closed", kind=None, mins=120, tokens=412_000), id="aa1b9a71-0000")
        to = datetime.fromtimestamp(wrapped["last"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        (folder / "handoffs" / (time.strftime("%Y-%m-%d-%H%M") + "-aa1b9a71.md")).write_text(
            f"---\nsession: aa1b9a71-0000\nname: Covered\nto: {to}\n---\n\nDone.\n")
        self.sessions = [wrapped, dict(session("Old", state="closed", kind=None, mins=120, tokens=412_000), id="bb2c0000-0000", **old)]
        ui = self.fresh()
        self.assertEqual([ui.wrapped(s) for s in ui.sessions], [True, False])  # the fixture
        return ui

    def real(self, acts):
        return [a for a in acts if a != DIV]

    def assert_resumable(self, ui, name):
        """name's actions offer Resume; leaves it the selected row."""
        acts = self.acts(ui, name)
        self.assertTrue([a for a in acts if a != DIV and a.startswith(self.RESUME)], f"no Resume action: {acts}")

    def test_handoff_first_then_resume_then_a_fresh_session(self):
        acts = self.real(self.acts(self.fixture(), "Old"))
        self.assertEqual(acts[0], "Write its Handoff from the transcript", acts)
        self.assertEqual(acts[1], "Resume this Session · 412k tokens, about 2× to re-read (824k tokens' worth)", acts)
        self.assertIn("Start a fresh Session for Title", acts)
        self.assertGreater(acts.index("Start a fresh Session for Title"), 1, acts)
        self.assertEqual(sum(a.startswith(self.RESUME) for a in acts), 1, acts)

    def test_resume_says_what_re_reading_costs_on_each_cache(self):
        ui = self.fixture()
        base = dict(ui.sessions[1])
        for keys, want in (({}, "about 2× to re-read (824k tokens' worth)"),  # no ttl: the 1-hour cache, cold
                           ({"ttl": 3600}, "about 2× to re-read (824k tokens' worth)"),
                           ({"ttl": 300}, "about 1.25× to re-read (515k tokens' worth)"),
                           ({"reply_at": time.time()}, "about 0.1× to re-read (41k tokens' worth)")):  # still warm
            with self.subTest(keys=keys):
                ui.sessions[1] = dict(base, **keys)
                acts = self.real(self.acts(ui, "Old"))
                self.assertIn(f"{self.RESUME} · 412k tokens, {want}", acts)

    def test_with_no_tokens_resume_is_just_its_name(self):
        acts = self.real(self.acts(self.fixture(tokens=0), "Old"))
        self.assertEqual(acts[1], self.RESUME, acts)
        self.assertEqual([a for a in acts if a.startswith(self.RESUME)], [self.RESUME])

    def test_resume_opens_it_in_a_new_window_and_a_plain_open_is_still_refused(self):
        ui = self.fixture()
        opened = []
        saved, roost.open_in_window = roost.open_in_window, lambda s, term: opened.append((s["id"], term)) or "opened"
        try:
            old = next(s for s in ui.sessions if s["name"] == "Old")
            self.assert_resumable(ui, "Old")
            act(ui, ui.keys_sessions, ui.sessions, self.RESUME)
            self.assertEqual(opened, [("bb2c0000-0000", "ghostty")])
            self.assertNotIn("fresh Session", ui.msg)  # not refused
            ui.msg = ""
            self.assertIsNone(ui.open_one(old))
            self.assertIn("fresh Session", ui.msg)
            self.assertEqual(opened, [("bb2c0000-0000", "ghostty")], "open_one still refuses it")
        finally:
            roost.open_in_window = saved

    def test_with_no_terminal_resume_returns_it_to_run_here(self):
        ui = self.fixture()
        ui.term = None
        opened = []
        saved, roost.open_in_window = roost.open_in_window, lambda s, term: opened.append(s["id"]) or "opened"
        try:
            self.assert_resumable(ui, "Old")
            got = act(ui, ui.keys_sessions, ui.sessions, self.RESUME)
            self.assertEqual((got or {}).get("id"), "bb2c0000-0000", got)
            self.assertEqual(opened, [])
        finally:
            roost.open_in_window = saved

    def test_a_wrapped_up_project_session_gets_no_resume(self):
        acts = self.real(self.acts(self.fixture(), "Wrapped"))
        self.assertEqual(acts[0], "Start a fresh Session for Title", acts)
        self.assertFalse([a for a in acts if a.startswith(self.RESUME)], acts)

    def test_running_and_projectless_sessions_get_no_resume(self):
        ui = self.fixture()
        ui.sessions += [dict(session("Live"), id="cc3d0000-0000"), dict(session("Loose", state="closed", kind=None, mins=30, tokens=9000),
                                                                         id="dd4e0000-0000")]
        for name in ("Live", "Loose"):
            with self.subTest(name=name):
                self.assertFalse([a for a in self.acts(ui, name) if a.startswith(self.RESUME)])


class SessionColumns(unittest.TestCase):
    """The Sessions tab's columns scale with the window, read off the drawn header and rows, inside the
    table's outline: NAME, LAST ACTIVE, CONTEXT, PROJECT."""
    setUp = TextualScreens.setUp
    proj = staticmethod(TextualScreens.proj)
    select = staticmethod(TextualScreens.select)
    run_on = ProjectRows.run_on
    TITLES = [("NAME", "NAME"), ("LAST ACTIVE", "LAST"), ("CONTEXT", "CONT"), ("PROJECT", "PROJ")]

    def fleet(self, names, label="Printer fleet"):
        """Running Sessions with these names (the first idle, the second waiting, the rest idle), at
        5m, 2h, 3d... ago with 181k, 35k, 9k... of context; the first two are in a project called label."""
        tokens, mins = [181000, 35000, 9000, 120000, 4000], [5, 120, 3 * 1440, 30, 50]
        self.sessions = [session(n, state="waiting" if i == 1 else "idle", mins=mins[i % 5], tokens=tokens[i % 5])
                         for i, n in enumerate(names)]
        return [self.proj("p1", self.sessions[:2], label=label)]

    def drawn(self, projects, w, sort=None, config=None):
        """The Sessions tab in a window w wide (it has no sidebar): its lines from the list's left edge, the
        list's lines (header first), the list's width, and each Session's status text, LAST ACTIVE value and
        PROJECT label as roost works them out."""
        seen = {}

        async def look(pilot):
            ui = pilot.app.ui
            if sort:
                ui.sort = sort
            pilot.app.redraw()
            await pilot.pause()
            seen["list"] = pilot.app.query_one(roost.SessionList).region.width
            seen["x"] = pilot.app.query_one(roost.SessionList).region.x
            seen["cells"] = {s["name"]: (ui.status_cell(s)[0], roost.ago(s), s.get("proj") or "") for s in ui.srows}
        # 40 rows: each Session takes three lines, and at 30 the table holds only two
        lines = self.run_on(projects, [open_view("Sessions"), look], size=(w, 40), tab="Sessions", config=config).lines
        lines = [l[seen["x"]:] for l in lines]  # the list's part of each line
        h = self.header_y(lines)
        edge = next((i for i in range(h + 1, len(lines)) if lines[i].strip().startswith("╰")), None)  # the outline's bottom
        assert edge is not None, "no outline's bottom edge under the column headers:\n" + "\n".join(lines[h:])
        return lines[h:edge], seen["list"], seen["cells"]

    @staticmethod
    def header_y(lines):
        """The row of the column headers: NAME is its first word, inside the table's outline."""
        h = next((i for i, l in enumerate(lines) if re.match(r"^[│ ]*NAME\b", l)), None)
        assert h is not None, "no column headers:\n" + "\n".join(lines)
        return h

    def starts(self, header):
        """Where each drawn column title starts."""
        x, at = {}, 0
        for title, prefix in self.TITLES:
            i = header.find(prefix, at)
            if i >= 0:
                x[title], at = i, i + 1
        return x

    @staticmethod
    def row(listing, name):
        return next(l for l in listing[1:] if name[:12] in l)

    def whole(self, listing, cells, w_titles=("NAME", "LAST ACTIVE", "PROJECT")):
        """True when every Session's name, LAST ACTIVE and PROJECT text is drawn whole."""
        for name, (status, when, label) in cells.items():
            row = self.row(listing, name)
            want = {"NAME": name, "LAST ACTIVE": when, "PROJECT": label}
            if any(want[t] and want[t] not in row for t in w_titles):
                return False
        return True

    SHORT = ["Printer fix", "Laptop refresh", "Badge audit"]  # the longest is 14

    # 1. NAME is its longest name + 2, whatever name_width says

    def test_name_grows_with_its_longest_name_not_a_fixed_width(self):
        """Same window (200), same everything but the longest name, 16 then 24 (both over NAME's floor of
        16): NAME is 8 wider less its share of the 8 spare columns it took (spread over four columns), so
        5 to 8 wider."""
        widths = {}
        for longest in ("Printer fix desk", "Printer fix for desk 12A"):  # 16, 24
            listing, _, _ = self.drawn(self.fleet([longest, "Laptop"]), 200)
            x = self.starts(listing[0])
            widths[len(longest)] = x["LAST ACTIVE"] - x["NAME"]
        self.assertGreaterEqual(widths[24] - widths[16], 5, widths)
        self.assertLessEqual(widths[24] - widths[16], 8, widths)

    def test_sessions_name_ignores_the_name_width_setting(self):
        headers = {}
        for setting in (None, 20, 60):
            listing, _, _ = self.drawn(self.fleet(self.SHORT), 160, config={} if setting is None else {"name_width": setting})
            headers[setting] = listing[0]
        self.assertEqual(headers[20], headers[None], headers)
        self.assertEqual(headers[60], headers[None], headers)

    # 2. Whole values on a wide window

    def test_every_value_shows_whole_on_a_wide_window(self):
        long_name = "Quarterly laptop refresh and printer rollout"  # 44
        label = "Office printer fleet consolidation, phase two"  # 45
        listing, _, cells = self.drawn(self.fleet([long_name, "Laptop refresh", "Badge audit"], label=label), 220)
        self.assertTrue(cells[long_name][2], cells)  # it has its project
        for name, (status, when, proj) in cells.items():
            row = self.row(listing, name)
            for text in (name, when, proj):
                self.assertIn(text, row, (name, row))
            self.assertNotIn("…", row)

    # 3. Headers never run into each other

    def test_a_header_never_runs_into_the_next_one(self):
        """Every pair of drawn headers has a space between the first's text (its sort arrow included)
        and the second's start, at widths from 100 to 200, sorted on LAST ACTIVE, CONTEXT, NAME."""
        projects = self.fleet(["Printer fix", "Laptop", "Badge audit for the front lobby doors"], label="Fleet")
        for sort in (("LAST ACTIVE", True), ("CONTEXT", True), ("NAME", False), ("CONTEXT", False)):
            arrow = "▾" if sort[1] else "▴"
            for w in (100, 115, 130, 160, 200):
                head = self.drawn(projects, w, sort=sort)[0][0]
                x = self.starts(head)
                order = [t for t, _ in self.TITLES if t in x]
                if sort[0] in x and w >= 130:
                    self.assertTrue(head[x[sort[0]]:].startswith(f"{sort[0]} {arrow}"), (sort, w, head))
                for a, b in zip(order, order[1:]):
                    text = head[x[a]:x[b]].rstrip()
                    self.assertLess(x[a] + len(text), x[b], (sort, w, a, b, head))
                    self.assertTrue(text.startswith(a), (sort, w, a, head))

    # 4. Values start under their headers; CONTEXT is right-aligned under its word

    def test_each_value_starts_in_its_column(self):
        projects = self.fleet(self.SHORT + ["Toner order"])
        for w in (120, 200):
            listing, _, cells = self.drawn(projects, w)
            x = self.starts(listing[0])
            context_end = x["CONTEXT"] + len("CONTEXT")
            for name, (status, when, label) in cells.items():
                row = self.row(listing, name)
                self.assertEqual(row.find(when, x["LAST ACTIVE"] - 2), x["LAST ACTIVE"], (w, name, row))
                s = next(q for q in self.sessions if q["name"] == name)
                ctx = roost.ktok(s["tokens"])
                self.assertEqual(row[context_end - len(ctx):context_end], ctx, (w, name, row))
                self.assertEqual(row[context_end - len(ctx) - 1], " ", (w, name, row))
                if label:
                    self.assertEqual(row.find(label, x["PROJECT"] - 2), x["PROJECT"], (w, name, row))

    def test_a_wrapped_up_sessions_dash_ends_under_context(self):
        """A wrapped-up Session's CONTEXT is "—", right-aligned under the word like a size."""
        old = dict(session("Old printer audit", state="closed", kind=None, mins=120), attach=None)
        self.sessions = self.sessions + [old]
        proj = self.proj("printer-fleet", [old], covered=[old])
        seen = {}

        async def where(pilot):
            seen["x"] = pilot.app.query_one(roost.SessionList).region.x
        lines = self.run_on([proj], [self.select(lambda ui: setattr(ui, "view", CLOSED)), where], size=(160, 30), tab="Sessions").lines
        lines = [l[seen["x"]:] for l in lines]  # the list's part of each line
        h = self.header_y(lines)
        x = self.starts(lines[h])
        row = next(l for l in lines[h + 1:] if "Old printer audit" in l)
        end = x["CONTEXT"] + len("CONTEXT")
        self.assertEqual(row[end - 1], "—", row)
        self.assertEqual(row[end - 2], " ", row)

    # 5. Spare width: NAME up to its longest + 2, then spread evenly

    def test_a_very_wide_windows_spare_width_is_spread_over_every_column(self):
        """At 240 with short values, each column is wider than its contents by about the same amount:
        no single column (PROJECT, last) takes the spare. A column's contents are its longest value or
        its title (with the sort arrow), whichever is longer, + 2; PROJECT runs to one column short of
        the outline's right edge."""
        listing, width, cells = self.drawn(self.fleet(self.SHORT), 240)
        x = self.starts(listing[0])
        self.assertEqual(list(x), [t for t, _ in self.TITLES], listing[0])
        right = listing[0].rstrip().rfind("│")  # the outline's right edge
        self.assertGreater(right, x["PROJECT"], listing[0])
        drawn = {"NAME": x["LAST ACTIVE"] - x["NAME"],
                 "LAST ACTIVE": x["CONTEXT"] - x["LAST ACTIVE"], "CONTEXT": x["PROJECT"] - x["CONTEXT"],
                 "PROJECT": right - 1 - x["PROJECT"]}
        values = {"NAME": [n for n in cells],
                  "LAST ACTIVE": [c[1] for c in cells.values()] + ["LAST ACTIVE ▾"],
                  "CONTEXT": [roost.ktok(s["tokens"]) for s in self.sessions], "PROJECT": [c[2] for c in cells.values()]}
        need = {t: max(len(v) for v in values[t] + [t]) + 2 for t in drawn}
        spare = {t: drawn[t] - need[t] for t in drawn}
        for t in drawn:
            self.assertGreaterEqual(spare[t], 10, (t, spare, drawn))
        self.assertLessEqual(max(spare.values()) - min(spare.values()), 3, (spare, drawn))

    # 6. A narrow window: PROJECT gives way first, then CONTEXT, LAST ACTIVE; NAME last

    def test_project_gives_way_first(self):
        """Find the narrowest window that shows every value whole; 3 columns narrower, only PROJECT
        is narrower: every other column starts where it did, and PROJECT is still drawn."""
        long_name = "Quarterly laptop refresh and badge audit"  # 40
        label = "Office printer fleet consolidation phase"  # 40
        projects = self.fleet([long_name, "Laptop refresh"], label=label)

        def fits(w):
            listing, _, cells = self.drawn(projects, w)
            return self.whole(listing, cells), listing
        lo, hi = 100, 240
        self.assertTrue(fits(hi)[0], "nothing shows whole even at 240")
        while lo < hi:
            mid = (lo + hi) // 2
            if fits(mid)[0]:
                hi = mid
            else:
                lo = mid + 1
        self.assertGreaterEqual(lo, 103, "the fixture needs longer values")
        full, narrow = fits(lo)[1], fits(lo - 3)[1]
        x, y = self.starts(full[0]), self.starts(narrow[0])
        self.assertIn("PROJECT", y, narrow[0])
        for t in ("NAME", "LAST ACTIVE", "CONTEXT", "PROJECT"):
            self.assertEqual(x[t], y[t], (t, full[0], narrow[0]))
        self.assertNotIn(label, self.row(narrow, long_name))
        self.assertIn(long_name, self.row(narrow, long_name))

    def test_then_context_then_last_active_and_name_keeps_its_width(self):
        """At 100 columns, a longer and longer name (50 to 96, by 2): PROJECT goes first, then CONTEXT,
        then LAST ACTIVE, each gone for good once gone, and NAME stays its longest name + 2 while any
        column after it is still drawn."""
        base = "Quarterly laptop refresh and badge audit for the front lobby doors and the annex and its loading dock"
        gone_at = {}
        for n in range(50, 98, 2):
            long_name = base[:n]
            listing, _, cells = self.drawn(self.fleet([long_name, "Laptop refresh"], label="Printer fleet"), 100)
            x = self.starts(listing[0])
            for t in ("PROJECT", "CONTEXT", "LAST ACTIVE"):
                if t in x:
                    self.assertNotIn(t, gone_at, (n, t, "back after it gave way", listing[0]))
                else:
                    gone_at.setdefault(t, n)
            after = [t for t, _ in self.TITLES[1:] if t in x]
            if after:
                self.assertGreaterEqual(x[after[0]] - x["NAME"], n + 2, (n, listing[0]))  # NAME whole
                self.assertIn(long_name, self.row(listing, long_name), n)
            for name, (status, when, label) in cells.items():
                row = self.row(listing, name)
                if "PROJECT" not in x:
                    self.assertNotIn("Printer fleet", row, n)
                if "CONTEXT" in x:  # LAST ACTIVE gives way only after CONTEXT: until then it's whole
                    self.assertIn(when, row, (n, row))
        self.assertEqual(set(gone_at), {"PROJECT", "CONTEXT", "LAST ACTIVE"}, gone_at)
        self.assertLess(gone_at["PROJECT"], gone_at["CONTEXT"], gone_at)
        self.assertLess(gone_at["CONTEXT"], gone_at["LAST ACTIVE"], gone_at)

@unittest.skipUnless(roost.App, "Textual isn't installed here: run the tests with ~/.roost/venv/bin/python")
class SessionRows(unittest.TestCase):
    """The Running and Closed views: the view row as words (the current one bold, underlined, green), the
    table in a rounded outline the tab's width with the view and its count in its top edge, the columns
    NAME, LAST ACTIVE, CONTEXT, PROJECT, and each Session on two lines (its row, then its status mark and
    its last message, dim) and a blank line. The panel under the table no longer shows the last message."""
    setUp = TextualScreens.setUp
    views_fixture = TextualScreens.views_fixture
    closed = staticmethod(TextualScreens.closed)
    look = staticmethod(TextualScreens.look)
    select = staticmethod(TextualScreens.select)
    keys = staticmethod(TextualScreens.keys)
    on_session = TextualScreens.on_session

    TONER = "Swapped the **toner** in `tray 2` and reprinted"
    TONER_PLAIN = "Swapped the toner in tray 2 and reprinted"
    LONG = "Imaged the spare laptops " + " ".join(f"step{i:02d}" for i in range(60))

    def run_app(self, steps, size=(120, 40)):
        """TextualScreens.run_app, 40 rows tall unless asked: at 30 the table holds only two Sessions."""
        return TextualScreens.run_app(self, steps, size=size)

    def rows_fixture(self):
        """Three running Sessions: Printer fix (idle, ○, a last message with ** and backticks), Laptop refresh
        (waiting, ◆, a last message far longer than the window), Badge audit (working, ●, no last message)."""
        self.sessions = [dict(session("Printer fix", mins=5, tokens=41000), summary=self.TONER),
                         dict(session("Laptop refresh", state="waiting", mins=10, tokens=9000), summary=self.LONG),
                         session("Badge audit", state="busy", mins=20, tokens=2000)]

    @staticmethod
    def box(app):
        """The Sessions table's outline: (top, bottom, left, right, lines), the rows of its top and bottom
        edges and the columns of its left and right edges, on the screen as drawn."""
        lines = screen_text(app)
        top = next((y for y, l in enumerate(lines) if y > app.ui.view_row_y and "╭" in l), None)
        assert top is not None, "no outline under the view row:\n" + "\n".join(lines)
        left, right = lines[top].index("╭"), lines[top].find("╮")
        assert right > left, f"the outline's top edge has no ╮: {lines[top]!r}"
        bottom = next((y for y in range(top + 1, len(lines)) if len(lines[y]) > left and lines[y][left] == "╰"), None)
        assert bottom is not None, "the outline has no bottom edge:\n" + "\n".join(lines)
        return top, bottom, left, right, lines

    @classmethod
    def name_y(cls, app, name):
        """The screen row of name's first line, inside the outline."""
        top, bottom, left, right, lines = cls.box(app)
        y = next((y for y in range(top + 1, bottom) if name in lines[y]), None)
        assert y is not None, f"{name} isn't inside the outline:\n" + "\n".join(lines[top:bottom + 1])
        return y

    # 1. The view row: words, the current one bold, underlined and green, counts dim

    def test_the_current_views_word_is_bold_underlined_and_green_and_the_others_and_every_count_dim(self):
        self.views_fixture()
        seen = {}

        def styles(app):
            y = app.ui.view_row_y
            line = screen_text(app)[y]
            out = {}
            for label in roost.VIEWS:
                m = re.search(rf"\b{label} (\d+)\b", line)
                assert m, f"{label} and its count aren't in the view row {line!r}"
                out[label] = ([style_at(app, y, x) for x in range(m.start(), m.start() + len(label))],
                              [style_at(app, y, x) for x in range(m.start(1), m.end(1))])
            return roost.VIEWS[app.ui.view], out, line
        steps = [open_view("Sessions")]
        for i in range(len(roost.VIEWS)):
            steps += [self.look(seen, i, styles), self.keys("right")]
        self.run_app(steps)
        green, muted = roost.GREEN.lower(), roost.MUTED.lower()
        self.assertEqual([seen[i][0] for i in range(len(roost.VIEWS))], list(roost.VIEWS), "the fixture: → went through each view")
        for i in range(len(roost.VIEWS)):
            current, out, line = seen[i]
            for label, (word, count) in out.items():
                with self.subTest(view=current, label=label):
                    if label == current:
                        self.assertTrue(all(color_of(st) == green and st.bold and st.underline for st in word),
                                        f"{label} is bold, underlined and green: {line!r}")
                    else:
                        self.assertEqual({color_of(st) for st in word}, {muted}, f"{label} is dim while {current} shows")
                        self.assertFalse([st for st in word if st.underline], f"{label} isn't underlined while {current} shows")
                    self.assertEqual({color_of(st) for st in count}, {muted}, f"{label}'s count is dim: {line!r}")

    # 2. The outline, its top edge, and what's inside it

    def test_the_table_is_a_rounded_outline_the_tabs_width_with_the_view_and_its_count_in_its_top_edge(self):
        self.views_fixture()  # 2 running, 3 closed
        seen = {}
        steps = [open_view("Sessions"), self.look(seen, "Running", self.box),
                 self.keys("right"), self.look(seen, "Closed", self.box)]
        app = self.run_app(steps)
        width = app.size.width
        for view, count in (("Running", 2), ("Closed", 3)):
            top, bottom, left, right, lines = seen[view]
            with self.subTest(view=view):
                edge = lines[top][left:right + 1]
                self.assertRegex(edge, rf"^╭─ {view} {count} ─+╮$")
                self.assertRegex(lines[bottom][left:right + 1], r"^╰─+╯$")
                self.assertLessEqual(left, 2, f"the outline starts at the tab's left: {lines[top]!r}")
                self.assertGreaterEqual(right, width - 3, f"the outline runs to the tab's right: {lines[top]!r}")
                for y in range(top + 1, bottom):
                    self.assertEqual((lines[y][left:left + 1], lines[y][right:right + 1]), ("│", "│"),
                                     f"row {y} has the outline's sides: {lines[y]!r}")
                self.assertIn("NAME", lines[top + 1], "the column headers are the outline's first line")
                self.assertEqual(lines[top + 2][left + 1:right].strip(), "", "a blank line under the headers")
                names = [s["name"] for s in self.sessions if (s["state"] == "closed") == (view == "Closed")]
                self.assertTrue(any(n in lines[top + 3] for n in names), f"a Session is under the blank line: {lines[top + 3]!r}")

    def test_the_outlines_title_is_bold_its_count_dim_and_its_edge_in_the_line_color(self):
        self.views_fixture()
        seen = {}

        def look(app):
            top, bottom, left, right, lines = self.box(app)
            m = re.search(r"Running (\d+)", lines[top])
            return ([style_at(app, top, x) for x in range(m.start(), m.start() + len("Running"))],
                    [style_at(app, top, x) for x in range(m.start(1), m.end(1))],
                    [style_at(app, top, x) for x in (left, left + 1, right)] + [style_at(app, bottom, left + 3)])
        self.run_app([open_view("Sessions"), self.look(seen, "x", look)])
        title, count, edges = seen["x"]
        self.assertTrue(all(st and st.bold for st in title), "the view's name is bold")
        self.assertEqual({color_of(st) for st in count}, {roost.MUTED.lower()}, "its count is dim")
        line = importlib.import_module("roost.app.widgets").LINE.lower()
        self.assertEqual({color_of(st) for st in edges}, {line}, "the outline is in the line color, as the Projects tab's are")

    def test_the_closed_views_section_headers_are_inside_the_outline(self):
        self.views_fixture()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "dismissed.json"
        spare = next(s for s in self.sessions if s["name"] == "Spare audit")
        path.write_text(json.dumps([spare["id"]]))
        patcher, _ = dismissed_file(path)
        self.addCleanup(patcher.stop)
        seen = {}
        self.run_app([open_view("Sessions"), self.keys("right"), self.look(seen, "x", self.box)])
        top, bottom, left, right, lines = seen["x"]
        for head in ("Closed (2)", "Dismissed (1)"):
            y = next((y for y, l in enumerate(lines) if head in l), None)
            with self.subTest(head=head):
                self.assertIsNotNone(y, "\n".join(lines))
                self.assertTrue(top < y < bottom, f"{head} is inside the outline:\n" + "\n".join(lines[top:bottom + 1]))
                self.assertGreater(lines[y].index(head), left)
                self.assertEqual(lines[y][right:right + 1], "│", lines[y])

    # 3. The columns: NAME, LAST ACTIVE, CONTEXT, PROJECT; no STATUS

    def test_the_columns_are_name_last_active_context_and_project_with_no_status(self):
        self.rows_fixture()
        seen = {}

        def look(app):
            top, bottom, left, right, lines = self.box(app)
            cells = {s["name"]: app.ui.status_cell(s)[0] for s in app.ui.srows}
            return lines[top + 1], {s["name"]: lines[self.name_y(app, s["name"])] for s in app.ui.srows}, cells
        self.run_app([open_view("Sessions"), self.look(seen, "x", look)])
        head, rows, cells = seen["x"]
        self.assertRegex(head, r"NAME.*\bLAST ACTIVE\b.*\bCONTEXT\b.*\bPROJECT\b")
        self.assertNotIn("STATUS", head)
        self.assertIn(roost.ktok(41000), rows["Printer fix"])  # 41k
        for name, row in rows.items():
            with self.subTest(name=name):
                self.assertNotIn(cells[name], row, "its status isn't a column on its row")
                self.assertFalse(set(row) & set("█▉▊▋▌▍▎▏░▒▓■"), f"CONTEXT has no bar: {row!r}")

    # 4. Two lines a Session: its row, then its status mark and its last message; a blank line after

    def lines_of(self, app):
        """For each Session in the view: its row's text, its second line's, and the line after them, with
        the selected row's › column, NAME's column, and each second line's status mark (the first character
        of status_cell's text) and its color as drawn and as its status is colored (status_cell's attr)."""
        top, bottom, left, right, lines = self.box(app)
        ui = app.ui
        out = {}
        for i, s in enumerate(ui.srows):
            y = self.name_y(app, s["name"])
            symbol = ui.status_cell(s)[0][0]
            mark = lines[y + 1].find(symbol)
            out[s["name"]] = {"symbol": symbol, "y": y, "row": lines[y], "second": lines[y + 1], "after": lines[y + 2] if y + 2 < bottom else None,
                              "mark": mark, "drawn": color_of(style_at(app, y + 1, mark)) if mark >= 0 else None,
                              "want": color_of(ui.style(ui.status_cell(s)[1])), "selected": i == ui.sel,
                              "msg": [style_at(app, y + 1, x) for x in range(mark + 1, right)]}
        return out, lines[top + 1].index("NAME"), left, right

    def test_each_session_is_its_row_then_its_status_mark_and_last_message_then_a_blank_line(self):
        self.rows_fixture()
        seen = {}
        self.run_app([open_view("Sessions"), self.on_session("Printer fix"), self.look(seen, "x", self.lines_of)])
        out, name_x, left, right = seen["x"]
        sel = out["Printer fix"]
        self.assertTrue(sel["selected"], "the fixture: Printer fix is selected")
        tick = sel["row"].find("›")
        self.assertTrue(left < tick < sel["row"].index("Printer fix"), f"› before the selected name: {sel['row']!r}")
        last = max(out, key=lambda n: out[n]["y"])
        for name, got in out.items():
            with self.subTest(name=name):
                self.assertEqual(got["second"][tick:tick + 1], got["symbol"],
                                 f"its status mark {got['symbol']} in the column where › sits: {got['second']!r}")
                self.assertEqual(got["mark"], tick, f"{got['symbol']} in the column where › sits: {got['second']!r}")
                self.assertEqual(got["drawn"], got["want"], f"{got['symbol']} is colored as its status is")
                self.assertEqual(got["second"][left + 1:tick].strip(), "", got["second"])
                if name != last:
                    self.assertIsNotNone(got["after"])
                    self.assertEqual(got["after"][left + 1:right].strip(), "", f"a blank line after {name}")
        self.assertNotEqual(out["Printer fix"]["want"], out["Laptop refresh"]["want"], "the fixture: two statuses, two colors")
        self.assertEqual({n: g["symbol"] for n, g in out.items()},
                         {"Printer fix": "○", "Laptop refresh": "◆", "Badge audit": "●"}, "the fixture: three status marks")
        toner = out["Printer fix"]["second"]
        self.assertEqual(toner.find(self.TONER_PLAIN), name_x, f"the last message, plain, starts under NAME: {toner!r}")
        self.assertEqual(toner[right:right + 1], "│")
        self.assertEqual(out["Badge audit"]["second"][left + 1:right].strip(), out["Badge audit"]["symbol"],
                         "no last message: just its status mark")

    def test_the_last_message_is_dim_on_one_line_clipped_to_the_outline(self):
        self.rows_fixture()
        seen = {}
        self.run_app([open_view("Sessions"), self.look(seen, "x", self.lines_of)])
        out, name_x, left, right = seen["x"]
        got = out["Laptop refresh"]
        shown = got["second"][name_x:right].rstrip()
        self.assertGreater(len(shown), 40, f"the message is drawn: {got['second']!r}")
        self.assertTrue(self.LONG.startswith(shown.rstrip("…").rstrip()), f"its start, clipped: {shown!r}")
        self.assertEqual(got["second"][right:right + 1], "│", "clipped inside the outline")
        self.assertEqual(got["after"][left + 1:right].strip(), "", f"not wrapped onto the next line: {got['after']!r}")
        x0 = name_x - got["mark"] - 1  # msg starts the column after the mark
        text = [st for st, ch in zip(got["msg"][x0:], got["second"][name_x:right]) if ch.strip()]
        self.assertTrue(text)
        self.assertEqual({color_of(st) for st in text}, {roost.MUTED.lower()}, "the last message is dim")

    def test_a_session_with_a_recap_shows_the_recaps_summary_plain(self):
        import unittest.mock
        loose = dict(session("Loose ends", mins=3), summary="the raw last message")
        self.sessions = [loose]
        recap = {"id": "loose-recap", "path": "/tmp/loose-recap.md", "summary": "Recap: cleared the **print** queue with `lpstat`",
                 "last": time.time(), "sessions": [loose["id"]], "folder": None, "covered": {}}
        seen = {}
        with unittest.mock.patch.object(roost, "read_recaps", lambda: [recap]):
            self.run_app([open_view("Sessions"), self.look(seen, "x", self.lines_of)])
        out, name_x, left, right = seen["x"]
        second = out["Loose ends"]["second"]
        self.assertEqual(second.find("Recap: cleared the print queue with lpstat"), name_x, second)
        self.assertNotIn("raw last message", second)

    # 5. Either line selects; ↑ ↓ go Session by Session; scrolling keeps both lines in view

    def test_a_click_on_either_of_a_sessions_lines_selects_it(self):
        self.rows_fixture()
        seen = {}

        def click(name, line):
            async def step(pilot):
                pilot.app.redraw()
                await pilot.pause()
                await pilot.click(offset=(30, self.name_y(pilot.app, name) + line))
                await pilot.pause()
                seen[(name, line)] = pilot.app.ui.srows[pilot.app.ui.sel]["name"]
            return step
        self.run_app([open_view("Sessions"), self.on_session("Printer fix"),
                      click("Badge audit", 1), click("Laptop refresh", 1), click("Printer fix", 0), click("Laptop refresh", 0)])
        self.assertEqual(seen, {("Badge audit", 1): "Badge audit", ("Laptop refresh", 1): "Laptop refresh",
                                ("Printer fix", 0): "Printer fix", ("Laptop refresh", 0): "Laptop refresh"})

    def test_up_and_down_move_session_by_session(self):
        self.rows_fixture()
        seen = {}
        name = lambda app: app.ui.srows[app.ui.sel]["name"]
        order = lambda app: [s["name"] for s in app.ui.srows]
        steps = [open_view("Sessions"), self.select(lambda ui: setattr(ui, "sel", 0)), self.look(seen, "order", order),
                 self.look(seen, 0, name)]
        for i, k in enumerate(["down", "down", "up"], 1):
            steps += [self.keys(k), self.look(seen, i, name)]
        self.run_app(steps)
        o = seen["order"]
        self.assertEqual([seen[i] for i in range(4)], [o[0], o[1], o[2], o[1]])

    def test_scrolling_keeps_the_selected_sessions_both_lines_in_view(self):
        self.sessions = [dict(session(f"Desk {i:02d}", mins=i + 1), summary=f"Note for desk {i:02d}") for i in range(14)]
        seen = []

        async def check(pilot):
            pilot.app.redraw()
            await pilot.pause()
            app = pilot.app
            s = app.ui.srows[app.ui.sel]
            top, bottom, left, right, lines = self.box(app)
            y = next((y for y in range(top + 1, bottom) if s["name"] in lines[y]), None)
            seen.append((s["name"], y is not None and top < y + 1 < bottom and s["summary"] in lines[y + 1],
                         "\n".join(lines[top:bottom + 1])))
        steps = [open_view("Sessions"), self.select(lambda ui: setattr(ui, "sel", 0)), check]
        for k in ["down"] * 13 + ["up"] * 13:
            steps += [self.keys(k), check]
        self.run_app(steps, size=(120, 30))
        self.assertEqual(len({n for n, _, _ in seen}), 14, "the fixture: ↓ reached every Session")
        for name, ok, shown in seen:
            self.assertTrue(ok, f"{name}'s row and its last message are both in view:\n{shown}")

    # 6. The panel keeps its facts but no longer shows the last message

    def test_the_panel_has_no_last_message_now_the_list_shows_it(self):
        self.rows_fixture()
        seen = {}

        def look(app):
            box = app.query_one(roost.SessionPanel).region
            lines = screen_text(app)
            return lines[box.y:box.y + box.height], lines[:box.y]
        self.run_app([open_view("Sessions"), self.on_session("Printer fix"), self.look(seen, "x", look)])
        panel, above = seen["x"]
        text = "\n".join(panel)
        self.assertIn("Printer fix", text, "the panel shows the selected Session")
        self.assertRegex(text, r"Project +none")
        self.assertIn("Context", text)
        self.assertTrue(any(self.TONER_PLAIN in l for l in above), "the list shows its last message")
        self.assertNotIn("Swapped the", text, "the panel doesn't")


_REAL_HERDR = roost.terminals.herdr  # the real boundary, for the tests that fake subprocess.run instead

# The variables that make a claude think it's a child session (and so not save its transcript). herdr's
# terminals can inherit them from whatever Claude Code session started herdr, so roost unsets them itself.
CHILD_SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT",
                      "CLAUDE_CODE_SSE_PORT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_SESSION_ATTENDED",
                      "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_PID")


def roost_environments():
    """Run the loop body once with roost's own environment holding every child-session variable (plus ones
    that must be left alone), once with none of them: what herdr is handed must not depend on either."""
    import unittest.mock
    inherited = dict({n: "1" for n in CHILD_SESSION_VARS}, CLAUDE_CODE_ENABLE_TELEMETRY="1")
    with unittest.mock.patch.dict(os.environ, inherited):
        yield "with the variables set"
    with unittest.mock.patch.dict(os.environ):
        for n in CHILD_SESSION_VARS:
            os.environ.pop(n, None)
        yield "with none of them set"


def assert_runs_claude_without_child_vars(test, argv, claude_argv):
    """argv is `env -u NAME ...` unsetting exactly the child-session variables, then claude_argv unchanged."""
    test.assertEqual(argv[:1], ["env"], argv)
    names, i = [], 1
    while i < len(argv) and argv[i] == "-u":
        names.append(argv[i + 1])
        i += 2
    test.assertEqual(sorted(names), sorted(CHILD_SESSION_VARS), argv)  # each once; PATH, telemetry etc. untouched
    test.assertEqual(argv[i:], claude_argv, argv)


class HerdrTerminal(unittest.TestCase):
    """herdr as a roost terminal: one workspace per project (labelled with the project id), one tab per
    Session (labelled with its name), all driven through terminals.herdr, the one boundary the tests replace."""
    README = "---\nsummary: s\nsessions: []\nstatus: active\n---\n\n# p\n"

    def setUp(self):
        import unittest.mock
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.saved = roost.CONFIG, roost.DONE, roost.RECAPS, roost.PLUGIN, roost.PROJECTS, roost.claude_settings, roost.NOTES
        roost.CONFIG, roost.DONE, roost.RECAPS, roost.NOTES = base / "config.json", base / "_done", base / "sessions", base / "notes"
        roost.claude_settings = lambda: {}
        roost.PLUGIN, roost.PROJECTS = base / "plugin", base / "transcripts"
        (roost.PLUGIN / ".claude-plugin").mkdir(parents=True)
        (roost.PLUGIN / ".claude-plugin" / "plugin.json").write_text("{}")
        self.root = base / "projects"
        self.root.mkdir()
        roost.save_config(projects_dir=str(self.root))
        self.folder = self.root / "p"
        (self.folder / "handoffs").mkdir(parents=True)
        (self.folder / "README.md").write_text(self.README)
        self.workdir = base / "work" / "repo"
        self.workdir.mkdir(parents=True)
        self.cwd = str(self.workdir)
        self.calls, self.herdr_down, self.workspaces, self.tabs = [], False, [], []
        self.osa_calls = []
        patches = [unittest.mock.patch.object(roost.terminals, "herdr", self.fake_herdr),
                   unittest.mock.patch.object(roost.terminals, "osa", lambda *a: self.osa_calls.append(a) or "ok")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        roost.CONFIG, roost.DONE, roost.RECAPS, roost.PLUGIN, roost.PROJECTS, roost.claude_settings, roost.NOTES = self.saved
        self.tmp.cleanup()

    def fake_herdr(self, *args):
        self.calls.append(list(args))
        if self.herdr_down:
            return None
        cmd = args[:2]
        if cmd == ("workspace", "list"):
            return {"type": "workspace_list", "workspaces": self.workspaces}
        if cmd == ("tab", "list"):
            return {"type": "tab_list", "tabs": self.tabs}
        if cmd == ("workspace", "create"):
            return {"type": "workspace_created", "workspace": {"workspace_id": "w9", "label": "x"},
                    "tab": {"tab_id": "w9:t1", "workspace_id": "w9", "label": "1"},
                    "root_pane": {"pane_id": "w9:p1", "tab_id": "w9:t1", "workspace_id": "w9"}}
        if cmd == ("tab", "create"):
            return {"type": "tab_created", "tab": {"tab_id": "w1:t7", "workspace_id": "w1", "label": "x"},
                    "root_pane": {"pane_id": "w1:p7", "tab_id": "w1:t7", "workspace_id": "w1"}}
        return {"type": "ok"}

    def made(self, *cmd):
        """The args of every herdr call that starts with cmd, in order."""
        return [c for c in self.calls if tuple(c[:len(cmd)]) == cmd]

    def flag(self, call, name):
        """The value after --name in a call."""
        return call[call.index(name) + 1]

    def project_sessions(self):
        return roost.frontmatter((self.folder / "README.md").read_text())["sessions"]

    # the terminal exists and is detected

    def test_herdr_is_a_listed_terminal(self):
        self.assertEqual(roost.terminals.TERMINALS["herdr"]["label"], "herdr")

    def test_herdr_env_wins_over_the_outer_terminal(self):
        import unittest.mock
        with unittest.mock.patch.dict(os.environ, {"HERDR_ENV": "1", "TERM_PROGRAM": "iTerm.app"}):
            os.environ.pop("HC_TERMINAL_ID", None)
            self.assertEqual(roost.terminals.detect_terminal(), "herdr")

    def test_without_herdr_env_the_outer_terminal_is_detected(self):
        import unittest.mock
        with unittest.mock.patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"}):
            os.environ.pop("HERDR_ENV", None)
            os.environ.pop("HC_TERMINAL_ID", None)
            self.assertEqual(roost.terminals.detect_terminal(), "iterm2")

    # starting a Session

    def test_start_in_an_existing_workspace_adds_a_tab_there_and_runs_claude(self):
        self.workspaces = [{"workspace_id": "w1", "label": "other"}, {"workspace_id": "w2", "label": "p"}]
        msg = roost.terminals.start_session("p", self.cwd, "herdr", "Fix it")
        self.assertEqual(self.made("workspace", "create"), [])
        (tab,) = self.made("tab", "create")
        self.assertEqual((self.flag(tab, "--workspace"), self.flag(tab, "--cwd"), self.flag(tab, "--label")),
                         ("w2", self.cwd, "Fix it"))
        self.assertIn("--focus", tab)
        (focus,) = self.made("workspace", "focus")
        self.assertEqual(focus[2], "w2")
        (run,) = self.made("pane", "run")
        self.assertEqual(run[2], "w1:p7")
        sid = re.search(r"--session-id ([0-9a-f-]{36})", run[3]).group(1)
        self.assertIn("/roost:pickup p", run[3])
        self.assertLess(self.calls.index(tab), self.calls.index(focus))
        self.assertLess(self.calls.index(focus), self.calls.index(run))
        self.assertFalse(msg.startswith("Couldn't"))
        self.assertEqual(self.project_sessions()[-1], {"name": "Fix it", "id": sid})

    def test_start_without_a_workspace_creates_one_and_renames_its_first_tab(self):
        self.workspaces = [{"workspace_id": "w1", "label": "other"}]
        msg = roost.terminals.start_session("p", self.cwd, "herdr", "Fix it")
        (create,) = self.made("workspace", "create")
        self.assertEqual((self.flag(create, "--cwd"), self.flag(create, "--label")), (self.cwd, "p"))
        self.assertIn("--focus", create)
        self.assertEqual(self.made("tab", "create"), [])
        (rename,) = self.made("tab", "rename")
        self.assertEqual(rename[2:], ["w9:t1", "Fix it"])
        (run,) = self.made("pane", "run")
        self.assertEqual(run[2], "w9:p1")
        self.assertIn("/roost:pickup p", run[3])
        self.assertLess(self.calls.index(create), self.calls.index(rename))
        self.assertLess(self.calls.index(rename), self.calls.index(run))
        self.assertFalse(msg.startswith("Couldn't"))
        self.assertEqual(len(self.project_sessions()), 1)

    def test_start_names_the_tab_after_the_project_when_no_name_is_given(self):
        self.workspaces = []
        roost.terminals.start_session("p", self.cwd, "herdr")
        self.assertEqual(self.made("tab", "rename")[0][2:], ["w9:t1", "p"])

    def test_a_failed_herdr_call_records_nothing(self):
        self.herdr_down = True
        msg = roost.terminals.start_session("p", self.cwd, "herdr", "Fix it")
        self.assertTrue(msg.startswith("Couldn't open"))
        self.assertEqual(self.project_sessions(), [])

    def test_a_herdr_call_failing_mid_way_records_nothing(self):
        self.workspaces = [{"workspace_id": "w2", "label": "p"}]
        real = self.fake_herdr
        roost.terminals.herdr = lambda *a: None if a[:2] == ("pane", "run") else real(*a)  # patched; undone by cleanup
        msg = roost.terminals.start_session("p", self.cwd, "herdr", "Fix it")
        self.assertTrue(msg.startswith("Couldn't open"))
        self.assertEqual(self.project_sessions(), [])

    def test_start_never_goes_through_applescript(self):
        self.workspaces = []
        roost.terminals.start_session("p", self.cwd, "herdr", "Fix it")
        self.assertEqual(self.osa_calls, [])

    # a Session with no project

    def test_plain_start_labels_workspace_and_tab_with_the_folder_name(self):
        self.workspaces = []
        msg = roost.terminals.start_plain(self.cwd, "herdr")
        (create,) = self.made("workspace", "create")
        self.assertEqual(self.flag(create, "--label"), "repo")
        self.assertEqual(self.made("tab", "rename")[0][2:], ["w9:t1", "repo"])
        (run,) = self.made("pane", "run")
        self.assertEqual(run[2], "w9:p1")
        self.assertIn("claude", run[3])
        self.assertNotIn("--session-id", run[3])
        self.assertFalse(msg.startswith("Couldn't"))

    def test_plain_start_reuses_the_folders_workspace(self):
        self.workspaces = [{"workspace_id": "w3", "label": "repo"}]
        roost.terminals.start_plain(self.cwd, "herdr")
        self.assertEqual(self.made("workspace", "create"), [])
        (tab,) = self.made("tab", "create")
        self.assertEqual((self.flag(tab, "--workspace"), self.flag(tab, "--label")), ("w3", "repo"))
        self.assertEqual(self.made("workspace", "focus")[0][2], "w3")
        self.assertEqual(self.made("pane", "run")[0][2], "w1:p7")

    # opening an existing Session

    def sess(self, **kw):
        return dict({"kind": "background", "name": "Fix it", "id": "11111111-2222-3333-4444-555555555555",
                     "cwd": self.cwd}, **kw)

    def test_an_interactive_session_is_focused_by_its_tab_label(self):
        self.tabs = [{"tab_id": "w1:t1", "workspace_id": "w1", "label": "1"},
                     {"tab_id": "w4:t2", "workspace_id": "w4", "label": "Fix it"}]
        msg = roost.terminals.open_in_window(self.sess(kind="interactive"), "herdr")
        self.assertTrue(msg.startswith("Switched to"))
        self.assertEqual(self.made("workspace", "focus")[0][2], "w4")
        self.assertEqual(self.made("tab", "focus")[0][2], "w4:t2")
        self.assertEqual(self.made("tab", "create") + self.made("pane", "run"), [])

    def test_an_attached_background_session_is_focused_too(self):
        self.tabs = [{"tab_id": "w4:t2", "workspace_id": "w4", "label": "Fix it"}]
        msg = roost.terminals.open_in_window(self.sess(attached=True), "herdr")
        self.assertTrue(msg.startswith("Switched to"))
        self.assertEqual(self.made("tab", "focus")[0][2], "w4:t2")

    def test_an_open_session_with_no_matching_tab_says_its_window_was_not_found(self):
        self.tabs = [{"tab_id": "w1:t1", "workspace_id": "w1", "label": "1"}]
        msg = roost.terminals.open_in_window(self.sess(kind="interactive"), "herdr")
        self.assertIn("is already open, but its window wasn't found", msg)
        self.assertEqual(self.made("tab", "focus") + self.made("workspace", "focus"), [])

    def test_a_closed_session_opens_a_tab_in_its_project_workspace_resuming_it(self):
        self.workspaces = [{"workspace_id": "w2", "label": "p"}]
        s = self.sess()
        msg = roost.terminals.open_in_window(s, "herdr", group="p")
        self.assertTrue(msg.startswith("Opened"))
        (tab,) = self.made("tab", "create")
        self.assertEqual((self.flag(tab, "--workspace"), self.flag(tab, "--label")), ("w2", "Fix it"))
        self.assertEqual(self.flag(tab, "--cwd"), self.cwd)
        self.assertEqual(self.made("workspace", "create"), [])
        (run,) = self.made("pane", "run")
        self.assertEqual(run[2], "w1:p7")
        self.assertIn(f"claude --resume {s['id']}", run[3])

    def test_a_background_session_with_an_attach_id_is_attached_to(self):
        self.workspaces = [{"workspace_id": "w2", "label": "p"}]
        roost.terminals.open_in_window(self.sess(attach="abcd1234"), "herdr", group="p")
        run = self.made("pane", "run")[0]
        self.assertIn("claude attach abcd1234", run[3])
        self.assertNotIn("--resume", run[3])

    def test_without_a_group_the_launch_folders_name_is_the_workspace(self):
        self.workspaces = []
        msg = roost.terminals.open_in_window(self.sess(), "herdr")
        self.assertTrue(msg.startswith("Opened"))
        self.assertEqual(self.flag(self.made("workspace", "create")[0], "--label"), "repo")
        self.assertEqual(self.made("tab", "rename")[0][2:], ["w9:t1", "Fix it"])
        self.assertIn("claude --resume", self.made("pane", "run")[0][3])

    def test_without_a_group_an_existing_folder_workspace_is_reused(self):
        self.workspaces = [{"workspace_id": "w3", "label": "repo"}]
        roost.terminals.open_in_window(self.sess(), "herdr")
        self.assertEqual(self.made("workspace", "create"), [])
        self.assertEqual(self.flag(self.made("tab", "create")[0], "--workspace"), "w3")

    def test_a_failed_herdr_call_while_opening_says_it_could_not(self):
        self.herdr_down = True
        msg = roost.terminals.open_in_window(self.sess(), "herdr", group="p")
        self.assertTrue(msg.startswith("Couldn't open"))

    # window titles

    def test_window_titles_are_the_tab_labels(self):
        self.tabs = [{"tab_id": "w1:t1", "workspace_id": "w1", "label": "Fix it"},
                     {"tab_id": "w1:t2", "workspace_id": "w1", "label": "Other"}]
        self.assertEqual(roost.terminals.window_titles("herdr"), ["Fix it", "Other"])

    def test_window_titles_are_empty_when_herdr_fails(self):
        self.herdr_down = True
        self.assertEqual(roost.terminals.window_titles("herdr"), [])

    def test_a_background_session_is_marked_open_by_its_tab_label(self):
        import unittest.mock
        self.tabs = [{"tab_id": "w1:t1", "workspace_id": "w1", "label": "Fix it"}]
        with unittest.mock.patch.object(roost.terminals, "attach_clients", lambda: set()):
            (s,) = roost.terminals.mark_open([self.sess()], "herdr")
        self.assertTrue(s["attached"])

    # inside herdr, roost is not in an IDE's terminal

    def test_herdr_is_not_an_ide_terminal_even_with_a_jetbrains_environment(self):
        env = {"HERDR_ENV": "1", "TERMINAL_EMULATOR": "JetBrains-JediTerm"}
        self.assertIsNone(roost.terminals.ide_terminal(env))

    def test_herdr_is_not_an_ide_terminal_even_with_a_vscode_environment(self):
        env = {"HERDR_ENV": "1", "TERM_PROGRAM": "vscode"}
        self.assertIsNone(roost.terminals.ide_terminal(env))

    def test_without_herdr_env_the_ide_is_still_read(self):
        self.assertEqual(roost.terminals.ide_terminal({"TERMINAL_EMULATOR": "JetBrains-JediTerm"}), "jetbrains")
        self.assertEqual(roost.terminals.ide_terminal({"TERM_PROGRAM": "vscode"}), "vscode")

    # the real herdr(): what it makes of a command's output

    def run_herdr(self, *args, returncode=0, stdout="", stderr="", raises=None):
        """_REAL_HERDR(*args) with subprocess.run faked to exit as given (or raise)."""
        import subprocess, unittest.mock

        def fake_run(argv, **kw):
            self.assertEqual(argv, ["herdr", *args])
            self.assertEqual(kw.get("stdin"), subprocess.DEVNULL)
            if raises:
                raise raises
            return subprocess.CompletedProcess(argv, returncode, stdout, stderr)
        with unittest.mock.patch.object(roost.terminals.subprocess, "run", fake_run):
            return _REAL_HERDR(*args)

    def test_a_command_that_prints_nothing_succeeds_with_an_empty_result(self):
        self.assertEqual(self.run_herdr("pane", "run", "w1:p1", "ls"), {})

    def test_a_command_that_prints_only_whitespace_succeeds_with_an_empty_result(self):
        self.assertEqual(self.run_herdr("pane", "run", "w1:p1", "ls", stdout=" \n\n"), {})

    def test_json_output_still_returns_its_result_object(self):
        out = '{"id": "1", "result": {"type": "ok"}}'
        self.assertEqual(self.run_herdr("tab", "focus", "w1:t1", stdout=out), {"type": "ok"})

    def test_a_nonzero_exit_is_a_failure_even_with_empty_output(self):
        self.assertIsNone(self.run_herdr("tab", "focus", "w1:t1", returncode=1, stderr="no such tab"))

    def test_output_that_is_not_json_is_a_failure(self):
        self.assertIsNone(self.run_herdr("tab", "list", stdout="Error: not a server\n"))

    def test_a_missing_binary_is_a_failure(self):
        self.assertIsNone(self.run_herdr("tab", "list", raises=FileNotFoundError("herdr")))

    def test_a_hang_is_a_failure(self):
        import subprocess
        self.assertIsNone(self.run_herdr("tab", "list", raises=subprocess.TimeoutExpired("herdr", 10)))

    def test_starting_a_session_works_when_herdr_prints_nothing_for_tab_rename_and_pane_run(self):
        import subprocess, unittest.mock
        ran = []

        def fake_run(argv, **kw):
            ran.append(argv[1:])
            shown = {("workspace", "list"): {"type": "workspace_list", "workspaces": []},
                     ("workspace", "create"): {"type": "workspace_created", "workspace": {"workspace_id": "w1", "label": "p"},
                                               "tab": {"tab_id": "w1:t1", "workspace_id": "w1", "label": "1"},
                                               "root_pane": {"pane_id": "w1:p1", "tab_id": "w1:t1", "workspace_id": "w1"}}}
            result = shown.get(tuple(argv[1:3]))
            out = json.dumps({"id": "x", "result": result}) if result else ""  # rename and run print nothing
            return subprocess.CompletedProcess(argv, 0, out, "")
        with unittest.mock.patch.object(roost.terminals, "herdr", _REAL_HERDR), \
                unittest.mock.patch.object(roost.terminals.subprocess, "run", fake_run):
            msg = roost.terminals.start_session("p", self.cwd, "herdr", "Fix it")
        self.assertFalse(msg.startswith("Couldn't"), msg)
        self.assertIn(["pane", "run"], [c[:2] for c in ran])
        self.assertEqual([x["name"] for x in self.project_sessions()], ["Fix it"])

    # claude in a herdr pane never runs as a child session, whatever herdr's terminal inherited

    def pane_command(self):
        import shlex
        (run,) = self.made("pane", "run")
        return shlex.split(run[3])

    def test_start_runs_claude_with_the_child_session_variables_unset(self):
        for env in roost_environments():
            with self.subTest(env):
                self.calls, self.workspaces = [], []
                roost.terminals.start_session("p", self.cwd, "herdr", "Fix it")
                argv = self.pane_command()
                sid = argv[argv.index("--session-id") + 1]
                assert_runs_claude_without_child_vars(self, argv, ["claude", "--session-id", sid, "-n", "Fix it",
                                                                   *roost.plugin_args(), "/roost:pickup p"])

    def test_plain_start_runs_claude_with_the_child_session_variables_unset(self):
        for env in roost_environments():
            with self.subTest(env):
                self.calls, self.workspaces = [], []
                roost.terminals.start_plain(self.cwd, "herdr")
                assert_runs_claude_without_child_vars(self, self.pane_command(), ["claude", *roost.plugin_args()])

    def test_resuming_runs_claude_with_the_child_session_variables_unset(self):
        for env in roost_environments():
            with self.subTest(env):
                self.calls, self.workspaces = [], [{"workspace_id": "w2", "label": "p"}]
                roost.terminals.open_in_window(self.sess(), "herdr", group="p")
                assert_runs_claude_without_child_vars(self, self.pane_command(),
                                                      ["claude", "--resume", self.sess()["id"]])

    def test_attaching_runs_claude_with_the_child_session_variables_unset(self):
        for env in roost_environments():
            with self.subTest(env):
                self.calls, self.workspaces = [], [{"workspace_id": "w2", "label": "p"}]
                roost.terminals.open_in_window(self.sess(attach="abcd1234"), "herdr", group="p")
                assert_runs_claude_without_child_vars(self, self.pane_command(), ["claude", "attach", "abcd1234"])


class HerdrAppTerminal(unittest.TestCase):
    """The Herdr desktop app ("herdr-app") as a roost terminal: one space per project (named with the project
    id), one tab per Session (named with its name), all driven through terminals.herdr_app, the one boundary
    the tests replace."""
    README, fake_herdr = HerdrTerminal.README, HerdrTerminal.fake_herdr
    made, flag, project_sessions = HerdrTerminal.made, HerdrTerminal.flag, HerdrTerminal.project_sessions

    def setUp(self):
        import unittest.mock
        HerdrTerminal.setUp(self)  # the temp project, config and plugin; self.cwd; herdr and osa replaced
        self.apps, self.down, self.spaces = [], False, []
        self.minimized = Path(self.tmp.name) / "minimized.json"
        for patcher in (unittest.mock.patch.object(roost.terminals, "herdr_app", self.fake_app),
                        unittest.mock.patch.object(roost.terminals.config, "MINIMIZED", self.minimized, create=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    tearDown = HerdrTerminal.tearDown

    def fake_app(self, *args):
        self.apps.append(list(args))
        if self.down:
            return None
        if args == ("ls",):
            return {"spaces": self.spaces, "recent": []}
        if args[:2] == ("do", "space.new"):
            return {"space": "spNEW", "tab": "tbNEW", "terminal": "t2"}
        if args[:2] == ("do", "tab.new"):
            return {"space": self.flag(list(args), "--space"), "tab": "tbNEW", "terminal": "t2"}
        if args[:2] == ("do", "terminal.park"):
            return {"pending": True, "terminal": self.flag(list(args), "--terminal")}
        if args[:2] == ("do", "terminal.keep"):
            return {"pending": True, "terminal": self.flag(list(args), "--terminal"), "pane": "pK", "tab": "tbKEPT"}
        return {"ok": True}

    def do(self, verb):
        """The args of every `do <verb>` call, in order."""
        return [c for c in self.apps if c[:2] == ["do", verb]]

    def argv(self, call):
        """The claude argv a call ran, after the `env -u ...` prefix (its own tests check that prefix)."""
        argv = json.loads(self.flag(call, "--argv"))
        n = len(roost.terminals.UNSET_CHILD_SESSION)
        return argv[n:] if argv[:n] == roost.terminals.UNSET_CHILD_SESSION else argv

    def space(self, sid, name, *tabs):
        return {"id": sid, "name": name, "folder": "/x", "active": False,
                "tabs": [{"id": t, "name": n, "active": False, "terminals": []} for t, n in tabs]}

    def sess(self, **kw):
        return dict({"kind": "background", "name": "Fix it", "id": "11111111-2222-3333-4444-555555555555",
                     "cwd": self.cwd}, **kw)

    # the terminal exists, and is detected

    def test_herdr_app_is_a_listed_terminal(self):
        self.assertEqual(roost.terminals.TERMINALS["herdr-app"]["label"], "Herdr")

    def test_the_apps_terminal_id_wins_over_ghostty_and_herdr_env(self):
        import unittest.mock
        env = {"HC_TERMINAL_ID": "t1", "TERM_PROGRAM": "ghostty", "HERDR_ENV": "1"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(roost.terminals.detect_terminal(), "herdr-app")

    def test_an_empty_terminal_id_is_not_the_app(self):
        import unittest.mock
        with unittest.mock.patch.dict(os.environ, {"HC_TERMINAL_ID": "", "TERM_PROGRAM": "ghostty"}, clear=True):
            self.assertEqual(roost.terminals.detect_terminal(), "ghostty")

    def test_the_app_is_not_an_ide_terminal_even_with_a_jetbrains_environment(self):
        env = {"HC_TERMINAL_ID": "t1", "TERMINAL_EMULATOR": "JetBrains-JediTerm"}
        self.assertIsNone(roost.terminals.ide_terminal(env))

    # starting a Session

    def test_start_in_an_existing_space_adds_a_tab_there_and_selects_the_space(self):
        self.spaces = [self.space("spA", "other"), self.space("spB", "p", ("tbX", "1"))]
        msg = roost.terminals.start_session("p", self.cwd, "herdr-app", "Fix it")
        self.assertEqual(self.apps[0], ["ls"])
        self.assertEqual(self.do("space.new"), [])
        (tab,) = self.do("tab.new")
        self.assertEqual((self.flag(tab, "--name"), self.flag(tab, "--space"), self.flag(tab, "--folder")),
                         ("Fix it", "spB", self.cwd))
        argv = self.argv(tab)
        sid = re.search(r"^[0-9a-f-]{36}$", argv[argv.index("--session-id") + 1]).group(0)
        self.assertEqual((argv[0], argv[-1]), ("claude", "/roost:pickup p"))
        (select,) = self.do("space.select")
        self.assertEqual(self.flag(select, "--id"), "spB")
        self.assertLess(self.apps.index(tab), self.apps.index(select))
        self.assertFalse(msg.startswith("Couldn't"))
        self.assertEqual(self.project_sessions()[-1], {"name": "Fix it", "id": sid})

    def test_start_without_a_space_creates_one_with_the_session_as_its_tab(self):
        self.spaces = [self.space("spA", "other")]
        msg = roost.terminals.start_session("p", self.cwd, "herdr-app", "Fix it")
        (new,) = self.do("space.new")
        self.assertEqual((self.flag(new, "--name"), self.flag(new, "--folder"), self.flag(new, "--tab")),
                         ("p", self.cwd, "Fix it"))
        argv = self.argv(new)
        self.assertEqual((argv[0], argv[-1]), ("claude", "/roost:pickup p"))
        self.assertIn("--session-id", argv)
        self.assertEqual(self.do("tab.new"), [])
        self.assertFalse(msg.startswith("Couldn't"))
        self.assertEqual(len(self.project_sessions()), 1)

    def test_a_failed_app_call_records_nothing(self):
        self.down = True
        msg = roost.terminals.start_session("p", self.cwd, "herdr-app", "Fix it")
        self.assertTrue(msg.startswith("Couldn't open"))
        self.assertEqual(self.project_sessions(), [])

    def test_a_failure_after_the_tab_exists_records_nothing(self):
        self.spaces = [self.space("spB", "p")]
        real = self.fake_app
        roost.terminals.herdr_app = lambda *a: None if a[:2] == ("do", "space.select") else real(*a)  # undone by cleanup
        msg = roost.terminals.start_session("p", self.cwd, "herdr-app", "Fix it")
        self.assertTrue(msg.startswith("Couldn't open"))
        self.assertEqual(self.project_sessions(), [])

    def test_starting_in_the_app_never_uses_applescript_or_herdr(self):
        self.spaces = []
        roost.terminals.start_session("p", self.cwd, "herdr-app", "Fix it")
        self.assertEqual((self.osa_calls, self.calls), ([], []))

    # a Session with no project

    def test_plain_start_names_space_and_tab_after_the_folder(self):
        self.spaces = []
        msg = roost.terminals.start_plain(self.cwd, "herdr-app")
        (new,) = self.do("space.new")
        self.assertEqual((self.flag(new, "--name"), self.flag(new, "--tab"), self.flag(new, "--folder")),
                         ("repo", "repo", self.cwd))
        argv = self.argv(new)
        self.assertEqual(argv[0], "claude")
        self.assertNotIn("--session-id", argv)
        self.assertFalse(msg.startswith("Couldn't"))

    def test_plain_start_reuses_the_folders_space(self):
        self.spaces = [self.space("spC", "repo")]
        roost.terminals.start_plain(self.cwd, "herdr-app")
        self.assertEqual(self.do("space.new"), [])
        (tab,) = self.do("tab.new")
        self.assertEqual((self.flag(tab, "--space"), self.flag(tab, "--name")), ("spC", "repo"))
        self.assertEqual(self.flag(self.do("space.select")[0], "--id"), "spC")

    # opening an existing Session

    def test_an_interactive_session_is_focused_by_its_tab_name(self):
        self.spaces = [self.space("spA", "x", ("tb1", "1")), self.space("spD", "y", ("tb9", "Fix it"))]
        msg = roost.terminals.open_in_window(self.sess(kind="interactive"), "herdr-app")
        self.assertTrue(msg.startswith("Switched to"))
        self.assertEqual(self.flag(self.do("space.select")[0], "--id"), "spD")
        self.assertEqual(self.flag(self.do("tab.select")[0], "--id"), "tb9")
        self.assertEqual(self.do("tab.new") + self.do("space.new"), [])

    def test_an_attached_background_session_is_focused_too(self):
        self.spaces = [self.space("spD", "y", ("tb9", "Fix it"))]
        msg = roost.terminals.open_in_window(self.sess(attached=True), "herdr-app")
        self.assertTrue(msg.startswith("Switched to"))
        self.assertEqual(self.flag(self.do("tab.select")[0], "--id"), "tb9")

    def test_an_open_session_with_no_matching_tab_says_its_window_was_not_found(self):
        self.spaces = [self.space("spA", "x", ("tb1", "1"))]
        msg = roost.terminals.open_in_window(self.sess(kind="interactive"), "herdr-app")
        self.assertIn("is already open, but its window wasn't found", msg)
        self.assertEqual(self.do("tab.select") + self.do("space.select"), [])

    def test_a_closed_session_opens_a_tab_in_its_project_space_resuming_it(self):
        self.spaces = [self.space("spB", "p")]
        s = self.sess()
        msg = roost.terminals.open_in_window(s, "herdr-app", group="p")
        self.assertTrue(msg.startswith("Opened"))
        (tab,) = self.do("tab.new")
        self.assertEqual((self.flag(tab, "--name"), self.flag(tab, "--space"), self.flag(tab, "--folder")),
                         ("Fix it", "spB", self.cwd))
        self.assertEqual(self.argv(tab), ["claude", "--resume", s["id"]])
        self.assertEqual(self.do("space.new"), [])

    def test_a_background_session_with_an_attach_id_is_attached_to(self):
        self.spaces = [self.space("spB", "p")]
        roost.terminals.open_in_window(self.sess(attach="abcd1234"), "herdr-app", group="p")
        self.assertEqual(self.argv(self.do("tab.new")[0]), ["claude", "attach", "abcd1234"])

    def test_without_a_group_the_launch_folders_name_is_the_space(self):
        self.spaces = []
        msg = roost.terminals.open_in_window(self.sess(), "herdr-app")
        self.assertTrue(msg.startswith("Opened"))
        (new,) = self.do("space.new")
        self.assertEqual((self.flag(new, "--name"), self.flag(new, "--tab")), ("repo", "Fix it"))
        self.assertEqual(self.argv(new), ["claude", "--resume", self.sess()["id"]])

    def test_without_a_group_an_existing_folder_space_is_reused(self):
        self.spaces = [self.space("spC", "repo")]
        roost.terminals.open_in_window(self.sess(), "herdr-app")
        self.assertEqual(self.do("space.new"), [])
        self.assertEqual(self.flag(self.do("tab.new")[0], "--space"), "spC")

    def test_a_failed_app_call_while_opening_says_it_could_not(self):
        self.down = True
        msg = roost.terminals.open_in_window(self.sess(), "herdr-app", group="p")
        self.assertTrue(msg.startswith("Couldn't open"))

    # window titles

    def test_window_titles_are_every_tab_name_across_spaces(self):
        self.spaces = [self.space("spA", "x", ("tb1", "Fix it"), ("tb2", "Other")), self.space("spB", "y", ("tb3", "Third"))]
        self.assertEqual(roost.terminals.window_titles("herdr-app"), ["Fix it", "Other", "Third"])

    def test_window_titles_are_empty_when_the_app_fails(self):
        self.down = True
        self.assertEqual(roost.terminals.window_titles("herdr-app"), [])

    def test_a_background_session_is_marked_open_by_its_tab_name(self):
        import unittest.mock
        self.spaces = [self.space("spA", "x", ("tb1", "Fix it"))]
        with unittest.mock.patch.object(roost.terminals, "attach_clients", lambda: set()):
            (s,) = roost.terminals.mark_open([self.sess()], "herdr-app")
        self.assertTrue(s["attached"])

    # the Sessions tab

    def test_the_sessions_tab_opens_a_project_session_in_its_projects_space(self):
        import unittest.mock
        roost.link_session(self.folder, "11111111-2222-3333-4444-555555555555", "Fix it")
        ui = Projects.ui(self)
        ui.term = "herdr-app"
        groups = []
        with unittest.mock.patch.object(roost.terminals, "open_in_window",
                                        lambda s, term, group=None: groups.append((term, group)) or "opened"):
            ui.open_window(self.sess())
        self.assertEqual(groups, [("herdr-app", "p")])

    # claude in a Herdr app tab never runs as a child session, whatever the app's terminal inherited

    def app_argv(self):
        """The --argv of the one space.new or tab.new call."""
        (call,) = self.do("space.new") + self.do("tab.new")
        return json.loads(self.flag(call, "--argv"))

    def check_each_space_case(self, start, claude_argv):
        """start() in a new space and in an existing one, under each roost environment; claude_argv(argv)
        is what claude should be run with."""
        for env in roost_environments():
            for spaces in ([], [self.space("spB", "p"), self.space("spC", "repo")]):
                with self.subTest(env, existing_space=bool(spaces)):
                    self.apps, self.spaces = [], spaces
                    start()
                    argv = self.app_argv()
                    assert_runs_claude_without_child_vars(self, argv, claude_argv(argv))

    def test_start_runs_claude_with_the_child_session_variables_unset(self):
        def expected(argv):
            sid = argv[argv.index("--session-id") + 1]
            return ["claude", "--session-id", sid, "-n", "Fix it", *roost.plugin_args(), "/roost:pickup p"]
        self.check_each_space_case(lambda: roost.terminals.start_session("p", self.cwd, "herdr-app", "Fix it"),
                                   expected)

    def test_plain_start_runs_claude_with_the_child_session_variables_unset(self):
        self.check_each_space_case(lambda: roost.terminals.start_plain(self.cwd, "herdr-app"),
                                   lambda argv: ["claude", *roost.plugin_args()])

    def test_resuming_runs_claude_with_the_child_session_variables_unset(self):
        self.check_each_space_case(lambda: roost.terminals.open_in_window(self.sess(), "herdr-app", group="p"),
                                   lambda argv: ["claude", "--resume", self.sess()["id"]])

    def test_attaching_runs_claude_with_the_child_session_variables_unset(self):
        self.check_each_space_case(
            lambda: roost.terminals.open_in_window(self.sess(attach="abcd1234"), "herdr-app", group="p"),
            lambda argv: ["claude", "attach", "abcd1234"])


    # minimizing: a Session's tab goes away while it keeps running, and comes back in its project's space

    def space_with(self, sid, name, *tabs):
        """A listed space; each tab is (tab id, tab name, [terminal addresses])."""
        return {"id": sid, "name": name, "folder": f"/work/{name}", "active": False,
                "tabs": [{"id": tid, "name": tname, "active": False,
                          "terminals": [{"terminal": t, "pane": f"p-{t}", "app": "claude-code", "sub_agents": []}
                                        for t in terms]} for tid, tname, terms in tabs]}

    def remembered(self):
        return json.loads(self.minimized.read_text()) if self.minimized.exists() else {}

    def record(self, s, **entry):
        self.minimized.write_text(json.dumps({s["id"]: dict({"terminal": "t7", "space": "p", "tab": "Fix it",
                                                             "folder": "/work/p"}, **entry)}))

    def stand_in(self, name, value):
        """Replace name in every roost module that defines it (in all of them while none does), until cleanup."""
        mods = [m for m in package.modules() if name in vars(m)]
        for m in mods or package.modules():
            if name in vars(m):
                self.addCleanup(setattr, m, name, vars(m)[name])
            else:
                self.addCleanup(lambda m=m: vars(m).pop(name, None))
            setattr(m, name, value)

    def running(self, foreground, files=None):
        """Herdr terminals as `hc get` reports them: foreground maps an address to its {pid, argv} (None for
        no foreground; an address left out makes hc fail). files maps a pid to its ~/.claude/sessions Session id.
        The real hc is never reached: PATH is emptied too."""
        import unittest.mock
        sessions = Path(self.tmp.name) / "claude-sessions"
        sessions.mkdir(exist_ok=True)
        for pid, sid in (files or {}).items():
            (sessions / f"{pid}.json").write_text(json.dumps({"pid": pid, "sessionId": sid, "cwd": "/x"}))
        self.hc_calls = []

        def hc(*args):
            self.hc_calls.append(list(args))
            if args[:1] != ("get",) or args[1] not in foreground:
                return None
            return {"terminal": args[1], "foreground": foreground[args[1]]}
        self.stand_in("hc", hc)
        self.stand_in("CLAUDE_SESSIONS", sessions)
        patcher = unittest.mock.patch.dict(os.environ, PATH=str(Path(self.tmp.name) / "no-bin"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def claude(self, pid, *args):
        return {"pid": pid, "argv": ["claude", *args]}

    # which Session a Herdr terminal is running

    def test_terminal_session_reads_the_session_file_of_the_terminals_process_first(self):
        self.running({"t1q": self.claude(501, "--resume", "old-id")}, files={501: "after-clear-id"})
        self.assertEqual(roost.terminal_session("t1q"), "after-clear-id")
        self.assertIn(["get", "t1q"], self.hc_calls)

    def test_terminal_session_falls_back_to_the_session_named_in_the_argv(self):
        for flag in ("--session-id", "--resume", "-r"):
            with self.subTest(flag):
                self.running({"t1q": self.claude(502, "--model", "opus", flag, "argv-id", "-n", "Fix it")})
                self.assertEqual(roost.terminal_session("t1q"), "argv-id")

    def test_terminal_session_is_none_when_nothing_names_a_session(self):
        cases = [("hc fails", {}), ("no foreground", {"t1q": None}),
                 ("claude with no session flag", {"t1q": self.claude(503, "--model", "opus")}),
                 ("a shell", {"t1q": {"pid": 504, "argv": ["-zsh"]}})]
        for what, foreground in cases:
            with self.subTest(what):
                self.running(foreground)
                self.assertIsNone(roost.terminal_session("t1q"))

    # minimizing: the tab of the terminal running the Session is parked, and where it was is recorded

    def test_minimize_parks_the_terminal_running_the_session_and_records_where_it_was(self):
        s = self.sess(kind="interactive")
        self.spaces = [self.space_with("spO", "other", ("tbO", "Notes", ["t3"])),
                       self.space_with("spP", "p", ("tbA", "Build", ["t5"]), ("tbS", "Renamed by hand", ["t6", "t7"]))]
        self.running({"t3": self.claude(1, "--resume", "someone-else"), "t5": None,
                      "t6": {"pid": 2, "argv": ["-zsh"]}, "t7": self.claude(3, "--resume", s["id"])})
        self.assertTrue(roost.terminals.app_minimize(s))
        self.assertEqual([self.flag(c, "--terminal") for c in self.do("terminal.park")], ["t7"])
        self.assertEqual(self.remembered(), {s["id"]: {"terminal": "t7", "space": "p", "tab": "Renamed by hand",
                                                       "folder": "/work/p"}})
        self.assertTrue(roost.terminals.is_minimized(s))

    def test_minimize_finds_a_session_by_any_of_its_ids(self):
        s = self.sess(kind="interactive", ids=["00000000-0000-0000-0000-00000000aaaa", self.sess()["id"]])
        self.spaces = [self.space_with("spP", "p", ("tbS", "Work", ["t7"]))]
        self.running({"t7": self.claude(9, "--session-id", "00000000-0000-0000-0000-00000000aaaa")})
        self.assertTrue(roost.terminals.app_minimize(s))
        self.assertEqual([self.flag(c, "--terminal") for c in self.do("terminal.park")], ["t7"])
        self.assertIn(s["id"], self.remembered())

    def test_minimize_never_picks_a_tab_by_its_name(self):
        s = self.sess(kind="interactive", ai_title="Fixing the thing")
        self.spaces = [self.space_with("spP", "p", ("tbA", "Fix it", ["t5"]), ("tbB", "Fixing the thing", ["t6"]))]
        self.running({"t5": self.claude(1, "--resume", "someone-else"), "t6": {"pid": 2, "argv": ["-zsh"]}})
        self.assertFalse(roost.terminals.app_minimize(s))
        self.assertEqual(self.do("terminal.park"), [])
        self.assertEqual(self.remembered(), {})
        self.assertFalse(roost.terminals.is_minimized(s))

    def test_a_failed_park_records_nothing(self):
        s = self.sess(kind="interactive")
        self.spaces = [self.space_with("spP", "p", ("tbS", "Fix it", ["t7"]))]
        self.running({"t7": self.claude(3, "--resume", s["id"])})
        real = self.fake_app
        roost.terminals.herdr_app = lambda *a: None if a[:2] == ("do", "terminal.park") else real(*a)  # undone by cleanup
        self.assertFalse(roost.terminals.app_minimize(s))
        self.assertEqual(self.remembered(), {})

    def test_is_minimized_exactly_when_its_id_is_recorded(self):
        s = self.sess(kind="interactive")
        self.assertFalse(roost.terminals.is_minimized(s))
        self.record(dict(s, id="99999999-0000-0000-0000-000000000000"))
        self.assertFalse(roost.terminals.is_minimized(s))
        self.record(s)
        self.assertTrue(roost.terminals.is_minimized(s))

    # restoring: the parked terminal gets a tab again, in its space, named as before

    def verbs(self):
        return [c[1] for c in self.apps if c[0] == "do"]

    def test_restore_into_its_space_selects_it_then_keeps_the_terminal_as_a_named_tab(self):
        s = self.sess(kind="interactive")
        self.record(s, tab="Renamed by hand")
        self.spaces = [self.space_with("spO", "other"), self.space_with("spP", "p", ("tbA", "Build", ["t5"]))]
        self.assertTrue(roost.terminals.app_restore(s))
        self.assertEqual(self.verbs(), ["space.select", "terminal.keep", "tab.rename"])  # selected first: the tab lands there
        self.assertEqual(self.flag(self.do("space.select")[0], "--id"), "spP")
        keep = self.do("terminal.keep")[0]
        self.assertEqual((self.flag(keep, "--terminal"), self.flag(keep, "--place")), ("t7", "tab"))
        rename = self.do("tab.rename")[0]
        self.assertEqual((self.flag(rename, "--id"), self.flag(rename, "--name")), ("tbKEPT", "Renamed by hand"))
        self.assertEqual(self.remembered(), {})
        self.assertFalse(roost.terminals.is_minimized(s))

    def test_restore_when_its_space_is_gone_makes_it_and_closes_its_placeholder_tab(self):
        s = self.sess(kind="interactive")
        self.record(s)
        self.spaces = [self.space_with("spO", "other")]
        self.assertTrue(roost.terminals.app_restore(s))
        self.assertEqual(self.verbs(), ["space.new", "space.select", "terminal.keep", "tab.rename", "tab.close"])
        made = self.do("space.new")[0]
        self.assertEqual((self.flag(made, "--name"), self.flag(made, "--folder")), ("p", "/work/p"))
        self.assertEqual(self.flag(self.do("space.select")[0], "--id"), "spNEW")
        self.assertEqual(self.flag(self.do("terminal.keep")[0], "--terminal"), "t7")
        close = self.do("tab.close")[0]
        self.assertEqual((self.flag(close, "--id"), self.flag(close, "--confirmed")), ("tbNEW", "true"))
        self.assertEqual(self.remembered(), {})

    def test_restore_of_a_session_never_minimized_does_nothing(self):
        self.spaces = [self.space_with("spP", "p")]
        self.assertFalse(roost.terminals.app_restore(self.sess(kind="interactive")))
        self.assertEqual(self.apps, [])

    def test_a_failed_restore_keeps_the_record_to_try_again(self):
        s = self.sess(kind="interactive")
        real = self.fake_app
        for spaces, failing in [([self.space_with("spP", "p")], "ls"), ([], "space.new"),
                                ([self.space_with("spP", "p")], "space.select"), ([self.space_with("spP", "p")], "terminal.keep")]:
            with self.subTest(failing):
                self.record(s)
                self.spaces = spaces
                roost.terminals.herdr_app = lambda *a, f=failing: None if a[:1] == (f,) or a[1:2] == (f,) else real(*a)
                self.assertFalse(roost.terminals.app_restore(s))
                self.assertIn(s["id"], self.remembered())  # herdr_app is put back by cleanup

    def test_opening_a_minimized_session_restores_it(self):
        s = self.sess(kind="interactive")
        self.record(s)
        self.spaces = [self.space_with("spP", "p", ("tbA", "Build", ["t5"]))]
        roost.terminals.open_in_window(s, "herdr-app", group="p")
        self.assertEqual([self.flag(c, "--terminal") for c in self.do("terminal.keep")], ["t7"])
        self.assertEqual(self.do("tab.new") + self.do("space.new"), [])  # the running claude comes back; none is started
        self.assertEqual(self.remembered(), {})


if __name__ == "__main__":
    unittest.main()
