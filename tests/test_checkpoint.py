from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import own_tests  # noqa: F401
import importlib
import unittest.mock

load_tests = own_tests(__name__)

SID = "aa1b9a71-0000-4000-8000-000000000001"
OTHER = "bb2c0000-0000-4000-8000-000000000002"
HOUR = 3600


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def usage():
    return {"input_tokens": 10, "output_tokens": 50, "cache_read_input_tokens": 5_000,
            "cache_creation_input_tokens": 100, "cache_creation": {"ephemeral_1h_input_tokens": 100}}


class Checkpoint(unittest.TestCase):
    """Hourly checkpoints: a project Session that keeps working gets a Handoff about once an hour, written
    from the Stop hook in the background, without touching the project's README."""

    def setUp(self):
        self.checkpoint = importlib.import_module("roost.core.checkpoint")
        self.summaries = importlib.import_module("roost.core.summaries")
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.saved = (roost.CONFIG, roost.DONE, roost.RECAPS, roost.NOTES, roost.PROJECTS, roost.LOG, roost.ROOST_HOME)
        roost.CONFIG, roost.DONE, roost.RECAPS, roost.NOTES = base / "config.json", base / "_done", base / "sessions", base / "notes"
        roost.PROJECTS, roost.LOG, roost.ROOST_HOME = base / "transcripts", base / "roost.log", base / "home"
        self.root = base / "projects"
        self.root.mkdir()
        roost.save_config(projects_dir=str(self.root), checkpoint=True)
        self.work = base / "work"
        self.work.mkdir()
        self.folder = self.project("printers", SID)
        # Wall-clock based, because a run attempt is recorded at the real time it happens.
        self.now = time.time()
        self.started_at = self.now - 3 * HOUR
        self.reply_at = self.now - 5 * 60
        self.transcript = self.write_transcript(SID, self.started_at, [self.reply_at])
        self.spawned, self.logged, self.summarized = [], [], []
        self.summary_error = None
        patches = [
            unittest.mock.patch.object(self.checkpoint, "spawn", self.spawned.append),
            unittest.mock.patch.object(self.summaries, "summarize", self.fake_summarize),
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

    def write_transcript(self, sid, started, replies):
        d = roost.PROJECTS / "-w"
        d.mkdir(parents=True, exist_ok=True)
        recs = [{"type": "custom-title", "customTitle": "Printer fix", "sessionId": sid},
                {"type": "user", "timestamp": iso(started), "sessionId": sid, "cwd": str(self.work),
                 "message": {"content": "go"}}]
        for i, t in enumerate(replies):
            recs.append({"type": "assistant", "timestamp": iso(t), "sessionId": sid, "cwd": str(self.work),
                         "message": {"id": f"m{i}", "usage": usage(), "content": [{"type": "text", "text": "ok"}]}})
        path = d / f"{sid}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in recs))
        return path

    def handoff_to(self, to, sid=SID, name="2026-10-09-1001-aa1b9a71.md"):
        (self.folder / "handoffs" / name).write_text(
            f"---\nsession: {sid}\nname: Printer fix\nfrom: \nto: {iso(to)}\n---\n\nDone.\n")

    def fake_summarize(self, sid, *a, **kw):
        self.summarized.append((sid, kw))
        if self.summary_error:
            raise ValueError(self.summary_error)
        return (self.folder / "handoffs" / "new.md", [], 0.12, False)

    def due(self, now=None, sid=SID, transcript=None):
        return self.checkpoint.due(sid, str(self.transcript if transcript is None else transcript),
                                   now=self.now if now is None else now)

    def stop_hook(self, stdin):
        with unittest.mock.patch("sys.stdin", io.StringIO(stdin)):
            return self.checkpoint.hook_command(None)

    # due(): config

    def test_a_working_project_session_with_no_handoff_is_due_an_interval_after_it_started(self):
        self.assertTrue(self.due())

    def test_with_checkpoint_never_set_nothing_is_due(self):
        roost.CONFIG.unlink()
        roost.save_config(projects_dir=str(self.root))
        self.assertFalse(self.due())

    def test_with_checkpoint_off_nothing_is_due(self):
        roost.save_config(checkpoint=False)
        self.assertFalse(self.due())

    def test_the_default_interval_is_an_hour(self):
        self.assertFalse(self.due(now=self.started_at + HOUR - 60))
        self.assertTrue(self.due(now=self.started_at + HOUR + 60))

    def test_the_interval_comes_from_config(self):
        roost.save_config(checkpoint_minutes=30)
        self.assertFalse(self.due(now=self.started_at + 29 * 60))
        self.assertTrue(self.due(now=self.started_at + 31 * 60))
        roost.save_config(checkpoint_minutes=240)
        self.assertFalse(self.due())

    # due(): which Sessions

    def test_a_session_no_project_lists_is_never_due(self):
        loose = "cc3d0000-0000-4000-8000-000000000003"
        path = self.write_transcript(loose, self.started_at, [self.reply_at])
        self.assertFalse(self.due(sid=loose, transcript=path))

    def test_a_missing_transcript_is_not_due_and_does_not_raise(self):
        self.assertFalse(self.due(transcript=roost.PROJECTS / "-w" / "missing.jsonl"))
        self.assertFalse(self.checkpoint.due(SID, "", now=self.now))
        self.assertFalse(self.checkpoint.due(SID, None, now=self.now))

    # due(): against the newest Handoff

    def test_a_reply_after_an_hour_old_handoff_makes_it_due(self):
        self.handoff_to(self.now - 2 * HOUR)
        self.assertTrue(self.due())

    def test_a_handoff_newer_than_an_interval_is_not_due_yet(self):
        self.handoff_to(self.now - 30 * 60)
        self.assertFalse(self.due())
        self.assertTrue(self.due(now=self.now - 30 * 60 + HOUR + 60))

    def test_nothing_new_since_its_handoff_is_never_due(self):
        self.handoff_to(self.reply_at)
        self.assertFalse(self.due())
        self.assertFalse(self.due(now=self.now + 24 * HOUR))

    def test_a_handoff_past_the_last_reply_is_never_due(self):
        self.handoff_to(self.reply_at + 60)
        self.assertFalse(self.due(now=self.now + 24 * HOUR))

    def test_the_newest_of_several_handoffs_counts(self):
        self.handoff_to(self.now - 2 * HOUR, name="2026-10-09-0800-aa1b9a71.md")
        self.handoff_to(self.now - 10 * 60, name="2026-10-09-0950-aa1b9a71.md")
        self.assertFalse(self.due())

    def test_another_sessions_handoff_does_not_count(self):
        self.handoff_to(self.reply_at, sid=OTHER)
        self.assertTrue(self.due())

    # due(): rollovers and earlier attempts

    def test_not_due_while_a_rollover_for_it_is_running(self):
        lock = roost.ROOST_HOME / "rollover" / f"{SID}.lock"
        lock.parent.mkdir(parents=True)
        lock.touch()
        self.assertFalse(self.due())

    def test_a_stale_rollover_lock_does_not_stop_it(self):
        lock = roost.ROOST_HOME / "rollover" / f"{SID}.lock"
        lock.parent.mkdir(parents=True)
        lock.touch()
        old = self.now - 2 * HOUR
        os.utime(lock, (old, old))
        self.assertTrue(self.due())

    def test_after_a_successful_run_it_is_not_due_again_until_an_interval_passes(self):
        self.assertTrue(self.checkpoint.run(SID))
        later = time.time()
        self.assertFalse(self.due(now=later + 60))
        self.assertTrue(self.due(now=later + HOUR + 60))

    def test_after_a_failed_run_it_is_not_retried_until_an_interval_passes(self):
        self.summary_error = "nothing to summarize"
        self.assertFalse(self.checkpoint.run(SID))
        later = time.time()
        self.assertFalse(self.due(now=later + 60))
        self.assertTrue(self.due(now=later + HOUR + 60))

    # run()

    def test_run_writes_the_handoff_without_the_readme(self):
        readme = (self.folder / "README.md").read_text()
        self.assertTrue(self.checkpoint.run(SID))
        (sid, kw), = self.summarized
        self.assertEqual(sid, SID)
        self.assertIs(kw.get("readme"), False)
        self.assertEqual((self.folder / "README.md").read_text(), readme)

    def test_a_failed_run_returns_false_and_logs_why(self):
        self.summary_error = "nothing to summarize"
        self.assertFalse(self.checkpoint.run(SID))
        self.assertTrue(any("nothing to summarize" in line for line in self.logged), self.logged)

    # hook_command(): roost hook stop

    def hook_json(self, sid=SID):
        return json.dumps({"session_id": sid, "transcript_path": str(self.transcript), "cwd": str(self.work),
                           "hook_event_name": "Stop"})

    def test_the_stop_hook_starts_a_checkpoint_when_one_is_due(self):
        self.assertEqual(self.stop_hook(self.hook_json()), 0)
        self.assertEqual(self.spawned, [SID])

    def test_the_stop_hook_starts_nothing_when_none_is_due(self):
        self.handoff_to(self.reply_at)
        self.assertEqual(self.stop_hook(self.hook_json()), 0)
        self.assertEqual(self.spawned, [])

    def test_the_stop_hook_reads_the_transcript_it_is_given(self):
        hook = json.loads(self.hook_json())
        hook["transcript_path"] = str(roost.PROJECTS / "-w" / "missing.jsonl")
        self.assertEqual(self.stop_hook(json.dumps(hook)), 0)
        self.assertEqual(self.spawned, [])

    def test_the_stop_hook_exits_zero_and_logs_on_bad_input(self):
        self.assertEqual(self.stop_hook("not json"), 0)
        self.assertEqual(self.spawned, [])
        self.assertTrue(self.logged, "the error is logged")

    def test_the_stop_hook_exits_zero_and_logs_when_the_check_raises(self):
        def boom(*a, **kw):
            raise RuntimeError("broken")
        with unittest.mock.patch.object(self.checkpoint, "due", boom):
            self.assertEqual(self.stop_hook(self.hook_json()), 0)
        self.assertTrue(any("broken" in line for line in self.logged), self.logged)


class Wiring(unittest.TestCase):
    """roost hook stop reaches the checkpoint hook, roost hook prompt still reaches rollover's, and the
    plugin registers the Stop hook."""

    def setUp(self):
        self.checkpoint = importlib.import_module("roost.core.checkpoint")
        self.rollover = importlib.import_module("roost.core.rollover")
        self.calls = []
        patches = [
            unittest.mock.patch.object(self.checkpoint, "hook_command", lambda args: self.calls.append("stop") or 0),
            unittest.mock.patch.object(self.rollover, "hook_command", lambda args: self.calls.append("prompt") or 0),
            unittest.mock.patch("sys.stdin", io.StringIO("{}"))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_hook_stop_runs_the_checkpoint_hook(self):
        self.assertEqual(roost.run_command("hook", ["stop"]), 0)
        self.assertEqual(self.calls, ["stop"])

    def test_hook_prompt_still_runs_the_rollover_hook(self):
        self.assertEqual(roost.run_command("hook", ["prompt"]), 0)
        self.assertEqual(self.calls, ["prompt"])

    def test_the_plugin_registers_a_stop_hook_that_runs_roost_hook_stop(self):
        hooks = json.loads((ROOT / "plugin" / "hooks" / "hooks.json").read_text())["hooks"]
        commands = [h.get("command", "") for group in hooks.get("Stop", []) for h in group.get("hooks", [])]
        self.assertTrue(any(re.search(r"\bhook stop\b", c) for c in commands), commands)
        prompts = [h.get("command", "") for group in hooks.get("UserPromptSubmit", []) for h in group.get("hooks", [])]
        self.assertTrue(any(re.search(r"\bhook prompt\b", c) for c in prompts), prompts)


if __name__ == "__main__":
    unittest.main()
