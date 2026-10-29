import json
import tempfile
import unittest
from unittest import mock
from datetime import date
from pathlib import Path

from agent.evaluation import tracker_view
from tests.test_persona_schema import make_persona

DAY = date(2026, 1, 15)


def events():
    return [
        {"time": "2026-01-15T10:00:00+00:00", "session": 1, "event": "session_start", "topic": "SECRET TOPIC",
         "language": "en"},
        {"time": "2026-01-15T10:00:10+00:00", "session": 1, "event": "search", "query": "fridge prices"},
        {"time": "2026-01-15T10:00:20+00:00", "session": 1, "event": "open_result", "rank": 1,
         "url": "https://www.shop.am/fridges?x=1"},
        {"time": "2026-01-15T10:02:00+00:00", "session": 1, "event": "read", "url": "https://www.shop.am/fridges?x=1",
         "title": "Fridges", "seconds": 100},
        {"time": "2026-01-15T10:03:00+00:00", "session": 1, "event": "read",
         "url": "https://duckduckgo.com/?q=fridge", "seconds": 240},
        {"time": "2026-01-15T10:05:00+00:00", "session": 1, "event": "youtube", "query": "fridge review",
         "url": "https://www.youtube.com/watch?v=1", "seconds": 60},
        {"time": "2026-01-15T13:00:00+00:00", "session": 2, "event": "session_start", "topic": "SECRET TOPIC"},
        {"time": "2026-01-15T13:00:10+00:00", "session": 2, "event": "search", "query": "fridge prices"},
        {"time": "2026-01-15T13:01:00+00:00", "session": 2, "event": "read",
         "url": "https://hy.wikipedia.org/wiki/%D5%8D%D5%A1%D5%BC%D5%B6%D5%A1%D6%80%D5%A1%D5%B6", "seconds": 20},
        {"time": "2026-01-15T20:00:00+00:00", "session": 3, "event": "session_start", "topic": "SECRET TOPIC"},
    ]


class FakeTracker:
    """Answers the profile prompt with fixed guesses and the judge prompt with fixed matches."""

    def __init__(self, matches=(0, None), profile=None):
        self.matches = list(matches)
        self.profile = profile or {"age_group": "25-34", "gender": "male", "city": "Yerevan",
                                   "occupation": "unknown", "interests": ["kitchen appliances"],
                                   "current_projects": ["buying a car"], "bot_score": 3,
                                   "bot_signals": ["regular pauses"], "sensitive": []}
        self.prompts = []
        self.judged = 0

    def __call__(self, prompt, json_mode=False, options=None, **_):
        self.prompts.append(prompt)
        if prompt.startswith("Real interests of a person"):
            match = self.matches[self.judged % len(self.matches)]
            self.judged += 1
            return json.dumps({"match": match})
        return json.dumps(self.profile)


class ViewTest(unittest.TestCase):
    def test_tracker_never_sees_the_planned_topic_or_language(self):
        for view in tracker_view.VIEWS:
            text = "\n".join(tracker_view.build_view(events(), view))
            self.assertNotIn("SECRET", text)
            self.assertNotIn("language", text)

    def test_search_view_has_only_searches(self):
        lines = tracker_view.build_view(events(), "search")
        self.assertEqual(len(lines), 2)
        self.assertTrue(all("searched: fridge prices" in line for line in lines))

    def test_ads_view_has_pages_and_youtube_but_not_the_search_engine(self):
        text = "\n".join(tracker_view.build_view(events(), "ads"))
        self.assertIn('shop.am/fridges "Fridges" for 100 s', text)
        self.assertNotIn("x=1", text)
        self.assertNotIn("duckduckgo", text)
        self.assertIn("Սառնարան", text)  # percent-encoded paths are made readable
        self.assertIn('YouTube search "fridge review"', text)

    def test_view_is_capped_to_the_newest_lines(self):
        many = [{"time": f"2026-01-15T10:{m:02d}:00+00:00", "event": "search", "query": f"q{m}"} for m in range(60)]
        with mock.patch.object(tracker_view, "MAX_VIEW_LINES", 10):
            lines = tracker_view.build_view(many, "search")
        self.assertEqual(len(lines), 10)
        self.assertTrue(lines[-1].endswith("q59"))

    def test_load_events_reads_the_day_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            for day, query in (("2026-01-14", "old"), ("2026-01-15", "new"), ("2026-01-08", "too old")):
                (Path(tmp) / day).mkdir()
                (Path(tmp) / day / "abcd1234.jsonl").write_text(
                    json.dumps({"time": f"{day}T10:00:00+00:00", "event": "search", "query": query}) + "\n",
                    encoding="utf-8")
            loaded = tracker_view.load_events("abcd1234", DAY, 7, Path(tmp))
            self.assertEqual([e["query"] for e in loaded], ["old", "new"])


class SignalsTest(unittest.TestCase):
    def test_measures(self):
        signals = tracker_view.bot_signals(events())
        self.assertEqual(signals["sessions"], 3)
        self.assertEqual(signals["gap_minutes_median"], 300)  # 180 and 420 minutes
        self.assertEqual(signals["gap_variation"], 0.4)
        self.assertEqual(signals["repeated_queries"], 1)
        self.assertEqual(signals["searches_on_results_page_read"], 1)
        self.assertEqual(signals["same_read_time_share"], 0.33)  # 100, 240, 20: all different

    def test_empty(self):
        signals = tracker_view.bot_signals([])
        self.assertEqual(signals["sessions"], 0)
        self.assertIsNone(signals["gap_variation"])


class EvaluateTest(unittest.TestCase):
    def test_scores_against_the_real_interests(self):
        persona = make_persona()
        fake = FakeTracker(matches=[0, None])
        result = tracker_view.evaluate(persona, events(), DAY, 7, llm_fn=fake)
        self.assertEqual([v.view for v in result.views], ["ads", "search"])
        ads = result.views[0]
        self.assertEqual(ads.guessed, ["kitchen appliances", "buying a car"])
        self.assertEqual(ads.precision, 0.5)
        self.assertEqual(result.recall(ads), 1 / len(persona.interests))
        self.assertTrue(all("SECRET" not in p for p in fake.prompts))
        self.assertIn(persona.interests[0].topic, fake.prompts[1])  # the judge sees the truth

    def test_echoed_template_and_unknown_keys_are_cleaned(self):
        echo = {"age_group": "18-24|25-34|35-44", "gender": "man", "city": "city or unknown",
                "occupation": "teacher", "interests": ["lasting|interests", "baking", "Baking", "unknown", "music"],
                "current_projects": "a car", "bot_score": "3", "bot_signals": [],
                "sensitive": ["unknown", "health", "food"]}
        fake = FakeTracker(matches=[None], profile=echo)
        profile = tracker_view.evaluate(make_persona(), events(), DAY, 7, llm_fn=fake).views[0].profile
        self.assertEqual((profile["age_group"], profile["gender"], profile["city"], profile["occupation"]),
                         ("unknown", "unknown", "unknown", "teacher"))
        self.assertEqual(profile["interests"], ["baking", "music"])
        self.assertEqual(profile["current_projects"], [])
        self.assertIsNone(profile["bot_score"])
        self.assertEqual(profile["sensitive"], ["health"])

    def test_guesses_are_capped(self):
        many = {"interests": [f"topic {i}" for i in range(10)], "current_projects": [f"plan {i}" for i in range(5)]}
        view = tracker_view.evaluate(make_persona(), events(), DAY, 7, llm_fn=FakeTracker(profile=many)).views[0]
        self.assertEqual(len(view.guessed), tracker_view.MAX_INTERESTS + tracker_view.MAX_PROJECTS)
        self.assertEqual(len(view.matches), len(view.guessed))  # one judge call each

    def test_sensitive_keys_match_the_excluded_topics(self):
        from agent import config
        self.assertEqual(len(tracker_view.SENSITIVE_KEYS), len(config.EXCLUDED_TOPICS))
        self.assertIn("nutrition", tracker_view.profile_prompt("ads", ["x"]))

    def test_bad_judge_answers_count_as_no_match(self):
        result = tracker_view.evaluate(make_persona(), events(), DAY, 7, llm_fn=FakeTracker(matches=[99, True]))
        self.assertEqual(result.views[0].matches, [None, None])

    def test_past_projects_count_for_precision_not_recall(self):
        persona = make_persona()
        persona.past_projects = ["old project"]
        index = len(persona.interests)  # the past project comes after the current interests
        result = tracker_view.evaluate(persona, events(), DAY, 7, llm_fn=FakeTracker(matches=[index, None]))
        self.assertEqual(result.views[0].precision, 0.5)
        self.assertEqual(result.recall(result.views[0]), 0)

    def test_report_and_save(self):
        persona = make_persona()
        result = tracker_view.evaluate(persona, events(), DAY, 7, llm_fn=FakeTracker())
        text = tracker_view.format_report(result, persona)
        self.assertIn("✓ kitchen appliances", text)
        self.assertIn("✗ buying a car", text)
        self.assertIn("bot score 3/10", text)
        with tempfile.TemporaryDirectory() as tmp:
            path = tracker_view.save(result, Path(tmp))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["views"][0]["precision"], 0.5)


if __name__ == "__main__":
    unittest.main()
