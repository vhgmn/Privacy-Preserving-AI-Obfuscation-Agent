import random
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from agent import config
from agent.persona import store as persona_store
from agent.planner import store as plan_store
from agent.planner.planner import plan_day
from agent.scheduler import timing
from agent.scheduler.daemon import SEARCH_PAUSE, Daemon
from agent.scheduler.rhythm import MIN_DAYS_TO_LEARN, Rhythm
from agent.scheduler.state import DayState
from tests.test_persona_schema import make_persona
from tests.test_planner import PlannerLLM, fake_translate

DAY = date(2026, 1, 15)
NOON = datetime(2026, 1, 15, 12, 0)
ACTIVE, AWAY = 10.0, 3600.0


class TimingTest(unittest.TestCase):
    def test_nothing_outside_waking_hours(self):
        night = datetime(2026, 1, 15, 3, 0)
        self.assertIsNone(timing.decide(night, ACTIVE, timing.DEFAULT_WAKING_HOURS, None, random.Random(1)))

    def test_waits_until_due(self):
        due = NOON + timedelta(minutes=10)
        self.assertIsNone(timing.decide(NOON, ACTIVE, timing.DEFAULT_WAKING_HOURS, due, random.Random(1)))
        self.assertEqual(timing.decide(due, ACTIVE, timing.DEFAULT_WAKING_HOURS, due, random.Random(1)), "full")

    def test_mostly_runs_while_user_is_active(self):
        rng = random.Random(3)
        away = [timing.decide(NOON, AWAY, timing.DEFAULT_WAKING_HOURS, None, rng) for _ in range(1000)]
        self.assertTrue(set(away) <= {"short", None})
        self.assertAlmostEqual(away.count("short") / 1000, timing.IDLE_SESSION_SHARE, delta=0.05)

    def test_gaps_are_bursty_and_bounded(self):
        rng = random.Random(4)
        gaps = [timing.next_gap(rng, 0, 600).total_seconds() / 60 for _ in range(500)]
        self.assertTrue(all(timing.MIN_GAP_MINUTES <= g <= timing.MAX_GAP_MINUTES for g in gaps))
        self.assertLess(min(gaps), 15)
        self.assertGreater(max(gaps), 120)

    def test_gaps_shrink_when_many_sessions_remain(self):
        relaxed = [timing.next_gap(random.Random(s), 1, 600) for s in range(200)]
        busy = [timing.next_gap(random.Random(s), 12, 120) for s in range(200)]
        self.assertLess(sum(busy, timedelta()), sum(relaxed, timedelta()))

    def test_remaining_waking_minutes(self):
        self.assertEqual(timing.remaining_waking_minutes(datetime(2026, 1, 15, 22, 30), timing.DEFAULT_WAKING_HOURS), 30)
        self.assertEqual(timing.remaining_waking_minutes(datetime(2026, 1, 15, 23, 30), timing.DEFAULT_WAKING_HOURS), 0)

    def test_short_version_is_one_search_and_one_read(self):
        plan, _ = plan_day(make_persona(), DAY, llm_fn=PlannerLLM(), translate_fn=fake_translate, rng=random.Random(1))
        short = timing.short_version(plan.sessions[0])
        self.assertEqual([s.type for s in short.steps], ["search", "open_result", "read"])


class RhythmTest(unittest.TestCase):
    def test_default_until_enough_days(self):
        rhythm = Rhythm()
        rhythm.record(datetime(2026, 1, 14, 3, 0), True)
        self.assertEqual(rhythm.waking_hours(DAY), timing.DEFAULT_WAKING_HOURS)

    def test_learns_waking_hours(self):
        rhythm = Rhythm()
        for d in range(MIN_DAYS_TO_LEARN):
            for hour in range(10, 20):
                rhythm.record(datetime(2026, 1, 1 + d, hour, 5), True)
        rhythm.record(datetime(2026, 1, 3, 2, 0), True)  # one sleepless night is not a habit
        self.assertEqual(rhythm.waking_hours(DAY), frozenset(range(10, 20)))

    def test_inactive_and_repeated_minutes_are_not_recorded(self):
        rhythm = Rhythm()
        self.assertFalse(rhythm.record(NOON, False))
        self.assertTrue(rhythm.record(NOON, True))
        self.assertFalse(rhythm.record(NOON + timedelta(minutes=5), True))

    def test_old_days_are_forgotten_and_file_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rhythm.json"
            rhythm = Rhythm()
            rhythm.record(datetime(2025, 11, 1, 9), True)
            rhythm.record(NOON, True)
            rhythm.save(path)
            loaded = Rhythm.load(path)
            self.assertEqual(list(loaded.days), ["2026-01-15"])
            path.write_text('{"days": {"x": [99]}}', encoding="utf-8")
            self.assertEqual(Rhythm.load(path).days, {})  # invalid file: start over rather than crash


class DaemonTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, value in (("DATA_DIR", root), ("PERSONAS_DIR", root / "personas")):
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.persona = make_persona()
        persona_store.save(self.persona)
        plan, _ = plan_day(self.persona, DAY, llm_fn=PlannerLLM(), translate_fn=fake_translate, rng=random.Random(2))
        plan_store.save(plan)
        self.sessions = len(plan.sessions)
        self.now = NOON
        self.idle = ACTIVE
        self.runs, self.toasts, self.prepared = [], [], []
        self.outcome = "done"

    def daemon(self):
        def run(persona, number, session, mode):
            self.runs.append((number, mode, len(session.steps)))
            return self.outcome

        def prepare(day):
            self.prepared.append(day)
            return True

        return Daemon(rng=random.Random(5), clock=lambda: self.now, idle=lambda: self.idle, echo=lambda _: None,
                      run_fn=run, prepare_fn=prepare, notify_fn=lambda t, m: self.toasts.append(t) or True)

    def test_prepares_once_then_runs_a_session_and_schedules_the_next(self):
        daemon = self.daemon()
        daemon.tick()
        self.assertEqual(self.prepared, [DAY])
        self.assertEqual(len(self.runs), 1)
        state = DayState.load(DAY)
        self.assertTrue(state.prepared)
        self.assertGreater(state.next_due, NOON)
        daemon.tick()  # not due yet
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(self.prepared, [DAY])

    def test_never_repeats_a_session_and_stops_when_all_done(self):
        daemon = self.daemon()
        for _ in range(self.sessions + 3):
            daemon.tick()
            self.now = DayState.load(DAY).next_due or self.now
        numbers = [n for n, _, _ in self.runs]
        self.assertEqual(sorted(numbers), list(range(1, self.sessions + 1)))

    def test_away_user_gets_only_short_sessions(self):
        self.idle = AWAY
        daemon = self.daemon()
        for _ in range(40):
            daemon.tick()
            self.now = max(self.now + timedelta(minutes=1), DayState.load(DAY).next_due or self.now)
            if self.now.hour >= 22:
                break
        self.assertTrue(self.runs)
        self.assertTrue(all(mode == "short" and steps == 3 for _, mode, steps in self.runs))

    def test_bot_check_pauses_persona_and_searches_and_notifies(self):
        self.outcome = "blocked"
        daemon = self.daemon()
        daemon.tick()
        state = DayState.load(DAY)
        self.assertIn(self.persona.id, state.paused_personas)
        self.assertEqual(state.search_paused_until, NOON + SEARCH_PAUSE)
        self.assertEqual(len(self.toasts), 1)
        self.now = NOON + timedelta(hours=5)
        daemon.tick()
        self.assertEqual(len(self.runs), 1)  # the persona stays paused for the day

    def test_nothing_at_night(self):
        self.now = datetime(2026, 1, 15, 3, 0)
        self.daemon().tick()
        self.assertEqual((self.runs, self.prepared), ([], []))


if __name__ == "__main__":
    unittest.main()
