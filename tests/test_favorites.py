import random
import unittest
from datetime import datetime, timezone

from agent.persona import favorites
from agent.persona.schema import Favorite
from agent.planner.schema import Session, Step
from agent.scheduler.timing import short_version
from tests.test_persona_schema import NOW, make_persona
from tests.test_planner import plan

SINCE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def read(url, seconds):
    return {"time": "2026-01-15T10:00:00+00:00", "event": "read", "url": url, "seconds": seconds}


class AlwaysYes(random.Random):
    def random(self):
        return 0.0  # adopts every candidate, forgets every favourite


class NeverYes(random.Random):
    def random(self):
        return 0.99


class CandidatesTest(unittest.TestCase):
    def test_long_reads_and_browsed_sites_count(self):
        events = [
            read("https://www.cultural.am/hy/article-1", 90),
            read("https://short.am/page", 20),  # a glance is not enough
            {"time": "2026-01-15T10:01:00+00:00", "event": "browse", "url": "https://hy.wikipedia.org/wiki/X"},
            read("https://duckduckgo.com/?q=x", 200),
            read("https://www.youtube.com/watch?v=1", 200),
            read("https://www.facebook.com/shop", 200),
            {"time": "2026-01-15T10:02:00+00:00", "event": "search", "query": "q"},
        ]
        self.assertEqual(favorites.candidates(events), {"cultural.am": "https://www.cultural.am/",
                                                        "hy.wikipedia.org": "https://hy.wikipedia.org/"})


class UpdateTest(unittest.TestCase):
    def test_adopts_by_chance_up_to_the_limit(self):
        events = [read(f"https://site{n}.am/a", 100) for n in range(8)]
        persona = favorites.update(make_persona(), events, AlwaysYes(), now=NOW)
        self.assertEqual(len(persona.favorites), 5)
        self.assertTrue(all(f.since == NOW for f in persona.favorites))
        self.assertIsNone(favorites.update(make_persona(), events, NeverYes(), now=NOW))

    def test_forgets_and_keeps_existing(self):
        old = Favorite("news.am", "https://news.am/", SINCE)
        persona = make_persona(favorites=[old])
        self.assertEqual(favorites.update(persona, [], AlwaysYes(), now=NOW).favorites, [])
        self.assertIsNone(favorites.update(persona, [read("https://news.am/x", 100)], NeverYes(), now=NOW))

    def test_original_is_untouched(self):
        persona = make_persona()
        favorites.update(persona, [read("https://a.am/x", 100)], AlwaysYes(), now=NOW)
        self.assertEqual(persona.favorites, [])


class PlanTest(unittest.TestCase):
    def persona(self):
        return make_persona(favorites=[Favorite("news.am", "https://news.am/", SINCE),
                                       Favorite("cultural.am", "https://www.cultural.am/", SINCE)])

    def test_without_favorites_there_are_no_visits(self):
        for seed in range(10):
            daily, _ = plan(seed=seed)
            self.assertFalse(any(s.type == "visit" for session in daily.sessions for s in session.steps))

    def test_favorites_bring_visits_and_habit_sessions(self):
        sessions = [s for seed in range(15) for s in plan(persona=self.persona(), seed=seed)[0].sessions]
        habits = [s for s in sessions if s.source == "habit"]
        with_visit = [s for s in sessions if s.source != "habit" and any(t.type == "visit" for t in s.steps)]
        self.assertTrue(habits and with_visit)
        self.assertTrue(all({t.type for t in s.steps} <= {"visit", "read"} for s in habits))
        urls = {t.url for s in sessions for t in s.steps if t.type == "visit"}
        self.assertLessEqual(urls, {"https://news.am/", "https://www.cultural.am/"})

    def test_plan_round_trips_with_visits(self):
        daily, _ = plan(persona=self.persona(), seed=4)
        self.assertEqual(type(daily).from_dict(daily.to_dict()).to_dict(), daily.to_dict())


class StepRulesTest(unittest.TestCase):
    def session(self, steps, source="long_term"):
        return Session(topic="t", source=source, language="en", engine="duckduckgo", steps=steps)

    def test_visit_needs_an_address_and_others_take_none(self):
        with self.assertRaises(ValueError):
            Step("visit")
        with self.assertRaises(ValueError):
            Step("read", url="https://x.am/")

    def test_no_result_to_open_after_a_visit(self):
        with self.assertRaises(ValueError):
            self.session([Step("visit", url="https://x.am/"), Step("read"), Step("open_result", rank=1)])
        self.session([Step("visit", url="https://x.am/"), Step("read"), Step("search", "fridge prices"),
                      Step("open_result", rank=1), Step("read")])

    def test_habit_sessions_have_no_searches(self):
        with self.assertRaises(ValueError):
            self.session([Step("visit", url="https://x.am/"), Step("read"), Step("search", "fridge")], "habit")

    def test_short_version_of_a_visit_is_one_page(self):
        session = self.session([Step("visit", url="https://x.am/"), Step("read"), Step("search", "fridge prices"),
                                Step("open_result", rank=1), Step("read")])
        self.assertEqual([s.type for s in short_version(session).steps], ["visit", "read"])

    def test_old_plans_still_load(self):
        daily, _ = plan(seed=1)
        data = daily.to_dict()
        data["schema_version"] = 1
        self.assertEqual(type(daily).from_dict(data).schema_version, 2)


if __name__ == "__main__":
    unittest.main()
