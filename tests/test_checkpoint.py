from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import own_tests  # noqa: F401
import importlib
import unittest.mock

load_tests = own_tests(__name__)

SID = "cc1b9a71-0000-4000-8000-000000000001"
STARTED, REPLY_AT = "2026-10-09T09:00:00.000Z", "2026-10-09T10:30:00.000Z"
HOUR = 3600


class Checkpoint(unittest.TestCase):
    """Hourly checkpoints: a working project Session gets a Handoff about once an hour, without its README."""

    def setUp(self):
        self.cp = importlib.import_module("roost.core.checkpoint")
        self.summaries = importlib.import_module("roost.core.summaries")
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.saved = (roost.CONFIG, roost.DONE, roost.RECAPS, roost.NOTES, roost.PROJECTS, roost.LOG, roost.ROOST_HOME)
        roost.CONFIG, roost.DONE, roost.RECAPS, roost.NOTES = base / "config.json", base / "_done", base / "sessions", base / "notes"
        roost.PROJECTS, roost.LOG, roost.ROOST_HOME = base / "transcripts", base / "roost.log", base / "home"
        self.root = base / "projects"
        self.root.mkdir()
        roost.save_config(projects_dir=str(self.root), checkpoint=True)
        self.folder = self.root / "printers"
        (self.folder / "handoffs").mkdir(parents=True)
        (self.folder / "README.md").write_text(f"---\nsummary: Printers\nsessions:\n  - name: Fix\n    id: {SID}\nstatus: active\n---\n")
        self.path = self.transcript(REPLY_AT)
        self.reply_at = roost.stamp(REPLY_AT)
        self.summarized, self.logged, self.spawned = [], [], []
        self.fail = None
        for p in [unittest.mock.patch.object(self.summaries, "summarize", self.fake_summarize),
                  unittest.mock.patch.object(self.cp, "spawn", self.spawned.append),
                  unittest.mock.patch.object(roost.config, "log", self.logged.append)]:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        roost.CONFIG, roost.DONE, roost.RECAPS, roost.NOTES, roost.PROJECTS, roost.LOG, roost.ROOST_HOME = self.saved
        self.tmp.cleanup()

    def transcript(self, reply_at, sid=SID):
        d = roost.PROJECTS / "-w"
        d.mkdir(parents=True, exist_ok=True)
        recs = [{"type": "user", "timestamp": STARTED, "sessionId": sid, "cwd": "/w", "uuid": "u1", "message": {"content": "go"}},
                {"type": "assistant", "timestamp": reply_at, "sessionId": sid, "cwd": "/w",
                 "message": {"id": "m1", "usage": {"input_tokens": 10, "output_tokens": 5}, "content": [{"type": "text", "text": "ok"}]}}]
        path = d / f"{sid}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in recs))
        return path

    def handoff(self, to):
        (self.folder / "handoffs" / f"2026-10-09-0000-{SID[:8]}.md").write_text(
            f"---\nsession: {SID}\nname: Fix\nfrom: {STARTED}\nto: {to}\n---\n\n# Handoff from Fix\n")

    def fake_summarize(self, sid, *a, **kw):
        self.summarized.append((sid, kw.get("readme")))
        if self.fail:
            raise ValueError(self.fail)
        return Path("/h.md"), [], 0.5, None

    def due(self, now, sid=SID, path=None):
        return self.cp.due(sid, str(path or self.path), now=now)

    def test_off_unless_turned_on(self):
        roost.save_config(projects_dir=str(self.root), checkpoint=False)
        self.assertFalse(self.due(self.reply_at + HOUR))

    def test_no_handoff_yet_waits_an_hour_from_its_start(self):
        started = roost.stamp(STARTED)
        self.assertFalse(self.due(started + HOUR - 1))
        self.assertTrue(self.due(started + HOUR))

    def test_an_hour_after_its_last_handoff_with_newer_work(self):
        self.handoff("2026-10-09T10:00:00.000Z")
        covered = roost.stamp("2026-10-09T10:00:00.000Z")
        self.assertFalse(self.due(covered + HOUR - 1))
        self.assertTrue(self.due(covered + HOUR))

    def test_nothing_new_since_its_handoff_is_never_due(self):
        self.handoff(REPLY_AT)
        self.assertFalse(self.due(self.reply_at + 5 * HOUR))

    def test_the_interval_comes_from_config(self):
        roost.save_config(projects_dir=str(self.root), checkpoint=True, checkpoint_minutes=30)
        self.assertTrue(self.due(roost.stamp(STARTED) + HOUR / 2))

    def test_sessions_no_project_lists_are_left_alone(self):
        other = "dd1b9a71-0000-4000-8000-000000000002"
        self.assertFalse(self.due(self.reply_at + 5 * HOUR, sid=other, path=self.transcript(REPLY_AT, other)))

    def test_a_missing_transcript_is_not_due_and_raises_nothing(self):
        self.assertFalse(self.due(self.reply_at + 5 * HOUR, path=Path(self.tmp.name) / "none.jsonl"))

    def test_it_writes_the_handoff_without_the_readme(self):
        self.assertTrue(self.cp.run(SID))
        self.assertEqual(self.summarized, [(SID, False)])

    def test_a_failed_checkpoint_waits_an_interval_before_trying_again(self):
        self.fail = "the model returned nothing"
        now = time.time()
        self.assertFalse(self.cp.run(SID))
        self.assertTrue(any("no Handoff written" in m for m in self.logged))
        self.assertFalse(self.due(now + HOUR - 60))  # not retried on every reply
        self.assertTrue(self.due(now + HOUR + 1))

    def test_not_during_a_rollover(self):
        lock = roost.ROOST_HOME / "rollover" / f"{SID}.lock"
        lock.parent.mkdir(parents=True)
        lock.touch()
        self.assertFalse(self.due(time.time()))

    def test_the_hook_spawns_when_due_and_never_fails(self):
        hook = json.dumps({"session_id": SID, "transcript_path": str(self.path)})
        with unittest.mock.patch.object(sys, "stdin", io.StringIO(hook)):
            self.assertEqual(self.cp.hook_command(None), 0)
        self.assertEqual(self.spawned, [SID])  # it started 2026-10-09, long over an hour ago
        with unittest.mock.patch.object(sys, "stdin", io.StringIO("not json")):
            self.assertEqual(self.cp.hook_command(None), 0)
        self.assertTrue(any(m.startswith("hook stop:") for m in self.logged))


if __name__ == "__main__":
    unittest.main()
