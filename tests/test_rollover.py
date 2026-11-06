from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import _SCRATCH, own_tests  # noqa: F401
import importlib
import unittest.mock

load_tests = own_tests(__name__)

SID = "aa1b9a71-0000-4000-8000-000000000001"
REPLY_AT = "2026-10-09T10:00:00.000Z"


def usage(context, one_hour=True):
    """A reply's usage whose context (input + cache read + cache creation) is `context` tokens."""
    made = {"ephemeral_1h_input_tokens": 100} if one_hour else {"ephemeral_5m_input_tokens": 100}
    return {"input_tokens": 10, "output_tokens": 50, "cache_read_input_tokens": context - 110,
            "cache_creation_input_tokens": 100, "cache_creation": made}


class Rollover(unittest.TestCase):
    """Automatic rollover: a prompt to a cold, big project Session is blocked, a Handoff is written, and a
    fresh Session opens with that prompt."""

    def setUp(self):
        self.rollover = importlib.import_module("roost.core.rollover")
        self.summaries = importlib.import_module("roost.core.summaries")
        self.terminals = importlib.import_module("roost.core.terminals")
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.saved = (roost.CONFIG, roost.DONE, roost.RECAPS, roost.NOTES, roost.PROJECTS, roost.LOG, roost.ROOST_HOME)
        roost.CONFIG, roost.DONE, roost.RECAPS, roost.NOTES = base / "config.json", base / "_done", base / "sessions", base / "notes"
        roost.PROJECTS, roost.LOG, roost.ROOST_HOME = base / "transcripts", base / "roost.log", base / "home"
        self.root = base / "projects"
        self.root.mkdir()
        roost.save_config(projects_dir=str(self.root), rollover=True)
        self.work = base / "work"
        self.work.mkdir()
        self.folder = self.project("printers", SID)
        self.reply_at = roost.stamp(REPLY_AT)
        self.transcript_path = self.write_transcript(SID, [("assistant", REPLY_AT, usage(150_000))])
        self.spawned, self.started, self.logged, self.summarized = [], [], [], []
        patches = [
            unittest.mock.patch.object(self.rollover, "spawn", lambda sid, prompt: self.spawned.append((sid, prompt))),
            unittest.mock.patch.object(self.summaries, "summarize", self.fake_summarize),
            unittest.mock.patch.object(self.terminals, "start_session", self.fake_start),
            unittest.mock.patch.object(self.terminals, "chosen_terminal", lambda: "fake-term"),
            unittest.mock.patch.object(roost.config, "log", self.logged.append)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        roost.CONFIG, roost.DONE, roost.RECAPS, roost.NOTES, roost.PROJECTS, roost.LOG, roost.ROOST_HOME = self.saved
        self.tmp.cleanup()

    def project(self, pid, *sids):
        folder = self.root / pid
        (folder / "handoffs").mkdir(parents=True)
        listed = "".join(f"  - name: Session {i}\n    id: {sid}\n" for i, sid in enumerate(sids))
        (folder / "README.md").write_text(f"---\nsummary: Printers\nsessions:\n{listed}status: active\n---\n\n# {pid}\n")
        return folder

    def write_transcript(self, sid, replies, sidechain=()):
        """replies: (type, iso time, usage or None) records; sidechain: (iso time, usage) records from a subagent."""
        d = roost.PROJECTS / "-w"
        d.mkdir(parents=True, exist_ok=True)
        recs = [{"type": "custom-title", "customTitle": "Printer fix", "sessionId": sid},
                {"type": "user", "timestamp": "2026-10-09T09:59:00.000Z", "sessionId": sid, "cwd": str(self.work),
                 "message": {"content": "go"}}]
        for i, (kind, ts, u) in enumerate(replies):
            recs.append({"type": kind, "timestamp": ts, "sessionId": sid, "cwd": str(self.work),
                         "message": {"id": f"m{i}", "usage": u, "content": [{"type": "text", "text": "ok"}]}})
        for i, (ts, u) in enumerate(sidechain):
            recs.append({"type": "assistant", "timestamp": ts, "sessionId": sid, "cwd": str(self.work), "isSidechain": True,
                         "message": {"id": f"s{i}", "usage": u, "content": []}})
        path = d / f"{sid}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in recs))
        return path

    def fake_summarize(self, sid, *a, **kw):
        self.summarized.append(sid)
        return None

    def fake_start(self, pid, cwd, term, name=None):
        self.started.append((pid, cwd, term, name))
        return "Started"

    def hook(self, prompt="next question", sid=SID):
        return {"session_id": sid, "transcript_path": str(roost.PROJECTS / "-w" / f"{sid}.jsonl"),
                "prompt": prompt, "cwd": str(self.work)}

    def cold(self):
        return self.reply_at + 3600 + 60

    def brief(self, pid="printers", session="bb2c0000-1111"):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(roost.run_command("project", ["brief", pid, "--session", session]), 0)
        return out.getvalue()

    # check(): when it blocks

    def test_a_cold_big_project_session_is_blocked_and_spawns_one_rollover(self):
        got = self.rollover.check(self.hook("fix the toner alert"), now=self.cold())
        self.assertEqual(got["decision"], "block")
        self.assertIn("printers", got["reason"])
        self.assertIn(">>", got["reason"])
        self.assertEqual(self.spawned, [(SID, "fix the toner alert")])

    def test_a_warm_cache_lets_the_prompt_through(self):
        self.assertIsNone(self.rollover.check(self.hook(), now=self.reply_at + 600))
        self.assertEqual(self.spawned, [])

    def test_a_one_hour_cache_is_still_warm_after_the_five_minute_mark(self):
        self.assertIsNone(self.rollover.check(self.hook(), now=self.reply_at + 301))

    def test_a_five_minute_cache_is_cold_after_five_minutes(self):
        self.write_transcript(SID, [("assistant", REPLY_AT, usage(150_000, one_hour=False))])
        self.assertEqual(self.rollover.check(self.hook(), now=self.reply_at + 301)["decision"], "block")
        self.assertIsNone(self.rollover.check(self.hook(), now=self.reply_at + 299))

    def test_a_small_context_lets_the_prompt_through(self):
        self.write_transcript(SID, [("assistant", REPLY_AT, usage(99_999))])
        self.assertIsNone(self.rollover.check(self.hook(), now=self.cold()))
        self.assertEqual(self.spawned, [])

    def test_a_context_at_the_default_threshold_is_blocked(self):
        self.write_transcript(SID, [("assistant", REPLY_AT, usage(100_000))])
        self.assertEqual(self.rollover.check(self.hook(), now=self.cold())["decision"], "block")

    def test_the_threshold_comes_from_config(self):
        roost.save_config(rollover_tokens=200_000)
        self.assertIsNone(self.rollover.check(self.hook(), now=self.cold()))
        roost.save_config(rollover_tokens=150_000)
        self.assertEqual(self.rollover.check(self.hook(), now=self.cold())["decision"], "block")

    def test_a_subagents_big_context_does_not_count(self):
        self.write_transcript(SID, [("assistant", REPLY_AT, usage(5_000))], sidechain=[(REPLY_AT, usage(300_000))])
        self.assertIsNone(self.rollover.check(self.hook(), now=self.cold()))

    def test_a_loose_session_lets_the_prompt_through(self):
        loose = "cc3d0000-0000-4000-8000-000000000003"
        self.write_transcript(loose, [("assistant", REPLY_AT, usage(150_000))])
        self.assertIsNone(self.rollover.check(self.hook(sid=loose), now=self.cold()))
        self.assertEqual(self.spawned, [])

    def test_with_rollover_off_the_prompt_goes_through(self):
        roost.save_config(rollover=False)
        self.assertIsNone(self.rollover.check(self.hook(), now=self.cold()))
        self.assertEqual(self.spawned, [])

    def test_with_rollover_never_set_the_prompt_goes_through(self):
        roost.CONFIG.unlink()
        roost.save_config(projects_dir=str(self.root))
        self.assertIsNone(self.rollover.check(self.hook(), now=self.cold()))
        self.assertEqual(self.spawned, [])

    def test_a_prompt_starting_with_two_arrows_goes_to_this_session(self):
        for prompt in (">> just answer this", "  \n>>no space"):
            self.assertIsNone(self.rollover.check(self.hook(prompt), now=self.cold()), prompt)
        self.assertEqual(self.spawned, [])

    def test_arrows_in_the_middle_of_a_prompt_do_not_count(self):
        self.assertEqual(self.rollover.check(self.hook("what does a >> b mean"), now=self.cold())["decision"], "block")

    # run(): the background work, and the lock check() sees

    def test_run_writes_the_handoff_then_starts_a_session_in_the_old_ones_folder(self):
        done = self.rollover.run(SID, "fix the toner alert")
        self.assertTrue(done)
        self.assertEqual(self.summarized, [SID])
        (pid, cwd, term, name), = self.started
        self.assertEqual((pid, cwd, term), ("printers", str(self.work), "fake-term"))
        self.assertTrue(isinstance(name, str) and name)

    def test_the_waiting_prompt_is_ready_before_the_new_session_starts(self):
        seen = []

        def start(pid, cwd, term, name=None):
            seen.append(self.brief(pid))
            return "Started"
        with unittest.mock.patch.object(self.terminals, "start_session", start):
            self.rollover.run(SID, "fix the toner alert")
        self.assertIn("## Waiting prompt", seen[0])
        self.assertIn("fix the toner alert", seen[0])

    def test_a_running_rollover_blocks_further_prompts_without_spawning_again(self):
        during = []

        def summarize(sid, *a, **kw):
            during.append(self.rollover.check(self.hook("another"), now=self.cold()))
        with unittest.mock.patch.object(self.summaries, "summarize", summarize):
            self.rollover.run(SID, "first")
        self.assertEqual(during[0]["decision"], "block")
        self.assertIn("already", during[0]["reason"])
        self.assertEqual(self.spawned, [])

    def test_the_lock_is_released_when_run_ends(self):
        self.rollover.run(SID, "first")
        self.assertEqual(self.rollover.check(self.hook("again"), now=self.cold())["decision"], "block")
        self.assertEqual(self.spawned, [(SID, "again")])

    def test_the_lock_is_released_when_summarizing_fails(self):
        def boom(sid, *a, **kw):
            raise ValueError("nothing to summarize")
        with unittest.mock.patch.object(self.summaries, "summarize", boom):
            self.rollover.run(SID, "first")
        self.assertEqual(self.rollover.check(self.hook("again"), now=self.cold())["decision"], "block")
        self.assertEqual(self.spawned, [(SID, "again")])

    def test_when_the_handoff_cannot_be_written_nothing_starts_and_no_prompt_is_left(self):
        def boom(sid, *a, **kw):
            raise ValueError("nothing to summarize")
        with unittest.mock.patch.object(self.summaries, "summarize", boom):
            done = self.rollover.run(SID, "fix the toner alert")
        self.assertFalse(done)
        self.assertEqual(self.started, [])
        self.assertTrue(self.logged, "the failure is logged")
        self.assertNotIn("Waiting prompt", self.brief())

    # run(): a Handoff that already covers the Session is not written again

    def handoff_to(self, to_iso, sid=SID, name="2026-10-09-1001-aa1b9a71.md"):
        (self.folder / "handoffs" / name).write_text(
            f"---\nsession: {sid}\nname: Printer fix\nfrom: \nto: {to_iso}\n---\n\nDone.\n")

    def test_a_handoff_that_reaches_the_last_reply_is_not_written_again(self):
        self.handoff_to(REPLY_AT)
        done = self.rollover.run(SID, "fix the toner alert")
        self.assertTrue(done)
        self.assertEqual(self.summarized, [])
        self.assertEqual([(pid, cwd, term) for pid, cwd, term, _ in self.started], [("printers", str(self.work), "fake-term")])
        brief = self.brief()
        self.assertIn("## Waiting prompt", brief)
        self.assertIn("fix the toner alert", brief)

    def test_a_handoff_that_reaches_past_the_last_reply_is_not_written_again(self):
        self.handoff_to("2026-10-09T11:00:00.000Z")
        self.assertTrue(self.rollover.run(SID, "next"))
        self.assertEqual(self.summarized, [])
        self.assertEqual(len(self.started), 1)

    def test_a_handoff_that_stops_before_the_last_reply_is_written_again(self):
        self.handoff_to("2026-10-09T09:59:30.000Z")
        self.assertTrue(self.rollover.run(SID, "next"))
        self.assertEqual(self.summarized, [SID])
        self.assertEqual(len(self.started), 1)

    def test_another_sessions_handoff_does_not_cover_this_one(self):
        self.handoff_to(REPLY_AT, sid="bb2c0000-0000-4000-8000-000000000002")
        self.rollover.run(SID, "next")
        self.assertEqual(self.summarized, [SID])

    def test_with_no_handoff_one_is_written(self):
        self.rollover.run(SID, "next")
        self.assertEqual(self.summarized, [SID])

    # the waiting prompt reaches the new Session through its brief

    def test_the_brief_prints_the_waiting_prompt_once(self):
        self.rollover.run(SID, "fix the toner alert\nand the tray")
        first = self.brief()
        self.assertIn("## Waiting prompt", first)
        self.assertIn("fix the toner alert\nand the tray", first)
        self.assertLess(first.index("# Project printers"), first.index("## Waiting prompt"))
        self.assertNotIn("## Waiting prompt", self.brief(session="cc3d0000-2222"))

    def test_a_brief_with_nothing_waiting_prints_no_such_section(self):
        self.assertNotIn("Waiting prompt", self.brief())

    def test_a_waiting_prompt_belongs_to_its_project(self):
        self.project("other")
        self.rollover.run(SID, "fix the toner alert")
        self.assertNotIn("Waiting prompt", self.brief("other"))
        self.assertIn("## Waiting prompt", self.brief("printers"))

    # a Session's first prompt: Claude Code names a transcript that isn't written yet

    def first_prompt_hook(self):
        hook = self.hook("first question")
        hook["transcript_path"] = str(roost.PROJECTS / "-w" / "not-written-yet.jsonl")
        return hook

    def test_a_transcript_that_does_not_exist_yet_lets_the_prompt_through(self):
        self.assertIsNone(self.rollover.check(self.first_prompt_hook(), now=self.cold()))
        self.assertEqual(self.spawned, [])

    def test_no_transcript_path_lets_the_prompt_through(self):
        hook = self.hook("first question")
        del hook["transcript_path"]
        self.assertIsNone(self.rollover.check(hook, now=self.cold()))
        hook["transcript_path"] = None
        self.assertIsNone(self.rollover.check(hook, now=self.cold()))
        self.assertEqual(self.spawned, [])

    def test_the_prompt_hook_logs_nothing_for_a_first_prompt(self):
        out = io.StringIO()
        with unittest.mock.patch("sys.stdin", io.StringIO(json.dumps(self.first_prompt_hook()))), \
                contextlib.redirect_stdout(out):
            code = roost.run_command("hook", ["prompt"])
        self.assertEqual((code, out.getvalue()), (0, ""))
        self.assertEqual(self.logged, [])


class HookCommand(unittest.TestCase):
    """roost hook prompt: the UserPromptSubmit hook. A broken hook must never block a prompt."""

    def setUp(self):
        self.rollover = importlib.import_module("roost.core.rollover")

    def run_hook(self, stdin, check):
        out = io.StringIO()
        with unittest.mock.patch("sys.stdin", io.StringIO(stdin)), unittest.mock.patch.object(self.rollover, "check", check), \
                contextlib.redirect_stdout(out):
            code = roost.run_command("hook", ["prompt"])
        return code, out.getvalue()

    def test_a_block_is_printed_as_json_for_claude_code(self):
        block = {"decision": "block", "reason": "rolling over"}
        seen = []
        code, out = self.run_hook(json.dumps({"session_id": "s1", "prompt": "hi"}), lambda hook, **kw: seen.append(hook) or block)
        self.assertEqual((code, json.loads(out)), (0, block))
        self.assertEqual(seen, [{"session_id": "s1", "prompt": "hi"}])

    def test_letting_the_prompt_through_prints_nothing(self):
        self.assertEqual(self.run_hook("{}", lambda hook, **kw: None), (0, ""))

    def test_input_that_is_not_json_prints_nothing_and_exits_zero(self):
        self.assertEqual(self.run_hook("not json", lambda hook, **kw: self.fail("not called")), (0, ""))

    def test_a_check_that_raises_prints_nothing_and_exits_zero(self):
        def boom(hook, **kw):
            raise RuntimeError("broken")
        self.assertEqual(self.run_hook("{}", boom), (0, ""))


if __name__ == "__main__":
    unittest.main()
