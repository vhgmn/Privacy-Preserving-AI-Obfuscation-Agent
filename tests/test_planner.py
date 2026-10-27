import json
import random
import re
import tempfile
import unittest
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from agent.persona.generator import GenerationError
from agent.persona.schema import Interest
from agent.planner import store
from agent.planner.planner import plan_day
from agent.planner.schema import DailyPlan, Session, Step, script_matches
from tests.test_persona_schema import NOW, make_persona

DAY = date(2026, 1, 15)


class PlannerLLM:
    """Writes numbered queries in the requested language; flags topics containing a sensitive word."""

    def __init__(self, sensitive=()):
        self.sensitive = set(sensitive)
        self.calls = []
        self.count = 0

    def __call__(self, prompt, json_mode=False, options=None, **_):
        self.calls.append(prompt)
        if prompt.startswith("Does this topic relate"):
            return json.dumps({"sensitive": any(word in prompt for word in self.sensitive)})
        wanted = int(re.search(r"Write (\d+)", prompt).group(1))
        russian = "Write them in Russian" in prompt
        queries = []
        for _ in range(wanted):
            self.count += 1
            queries.append(f"запрос номер {self.count}" if russian else f"query number {self.count}")
        return json.dumps({"queries": queries})


def fake_translate(texts, target):
    return [f"հարցում {text.split()[-1]}" for text in texts]


def plan(persona=None, llm_fn=None, translate_fn=fake_translate, seed=3, now=NOW):
    return plan_day(persona or make_persona(), DAY, llm_fn=llm_fn or PlannerLLM(),
                    translate_fn=translate_fn, rng=random.Random(seed), now=now)


class ScriptTest(unittest.TestCase):
    def test_script_matches(self):
        good = [("սառնարան գին", "hy"), ("LG սառնարան", "hy"), ("холодильник LG цена", "ru"),
                ("fridge prices 2026", "en")]
        bad = [("fridge price", "hy"), ("հűtարակ", "hy"), ("холодильник", "hy"), ("fridge", "ru"),
               ("սառնարան", "en"), ("холодильник", "en")]
        for text, lang in good:
            self.assertTrue(script_matches(text, lang), (text, lang))
        for text, lang in bad:
            self.assertFalse(script_matches(text, lang), (text, lang))


class PlanSchemaTest(unittest.TestCase):
    def session(self, steps, language="en"):
        return Session("chess", "long_term", language, "duckduckgo", steps)

    def test_round_trip(self):
        p, _ = plan()
        self.assertEqual(DailyPlan.from_dict(p.to_dict()), p)

    def test_rejects_bad_sessions(self):
        cases = {
            "starts with open_result": [Step("open_result", rank=1)],
            "starts with replaceable search": [Step("search", "chess openings", replaceable=True)],
            "read without open_result": [Step("search", "chess openings"), Step("read")],
            "wrong script": [Step("search", "шахматы")],
            "too many words": [Step("search", "one two three four five six seven eight nine")],
            "duplicate searches": [Step("search", "chess"), Step("search", "Chess", replaceable=True)],
        }
        for label, steps in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                self.session(steps)

    def test_rejects_bad_steps(self):
        for label, kwargs in {"rank 6": dict(type="open_result", rank=6), "read with query": dict(type="read", query="x"),
                              "replaceable youtube": dict(type="youtube", query="x", replaceable=True)}.items():
            with self.subTest(label), self.assertRaises(ValueError):
                Step(**kwargs)


class PlannerTest(unittest.TestCase):
    def test_plan_shape(self):
        p, warnings = plan()
        self.assertEqual(warnings, [])
        self.assertTrue(2 <= len(p.sessions) <= 5)
        for session in p.sessions:
            searches = [s for s in session.steps if s.type == "search"]
            self.assertFalse(searches[0].replaceable)
            self.assertTrue(all(s.replaceable for s in searches[1:]))
            self.assertTrue(2 <= len(searches) <= 4)

    def test_languages_follow_persona_and_armenian_is_translated(self):
        languages = set()
        for seed in range(8):
            p, _ = plan(seed=seed)
            for session in p.sessions:
                languages.add(session.language)
                if session.language == "hy":
                    self.assertTrue(all(s.query.startswith("հարցում") for s in session.steps if s.query))
        self.assertEqual(languages, {"hy", "ru", "en"})

    def test_safety_check_reads_english_not_armenian(self):
        fake = PlannerLLM()
        plan(llm_fn=fake, persona=make_persona(languages={"hy": 1.0}))
        safety = [c for c in fake.calls if c.startswith("Does this topic relate")]
        self.assertTrue(safety)
        self.assertTrue(all("query number" in c and "հարցում" not in c for c in safety))

    def test_without_translator_no_armenian_sessions(self):
        with mock.patch("agent.planner.planner.translate.is_available", return_value=False):
            p, warnings = plan(translate_fn=None, persona=make_persona(languages={"hy": 0.9, "en": 0.1}))
        self.assertTrue(all(s.language != "hy" for s in p.sessions))
        self.assertTrue(any("translator" in w for w in warnings))

    def test_expired_projects_are_not_planned(self):
        later = NOW + timedelta(weeks=4)  # "washing machine" has expired
        for seed in range(6):
            p, _ = plan(seed=seed, now=later)
            self.assertNotIn("washing machine", {s.topic for s in p.sessions})

    def test_topic_repeats_are_capped(self):
        # one heavy project: without the cap it would fill most sessions
        persona = make_persona(interests=[
            Interest("chess", "Plays online.", "long_term", 0.05, NOW),
            Interest("jazz", "Collects records.", "long_term", 0.05, NOW),
            Interest("new fridge", "Choosing one.", "project", 0.9, NOW, NOW + timedelta(weeks=2)),
        ])
        for seed in range(10):
            counts = Counter(s.topic for s in plan(persona=persona, seed=seed)[0].sessions)
            self.assertLessEqual(counts["new fridge"], 2)
            # 3 topics, up to 5 sessions: extra sessions go to the least-used topics
            self.assertLessEqual(max(counts.values()) - min(counts.values()), 1 if len(counts) == 3 else 2)

    def test_sensitive_sessions_are_skipped(self):
        p, warnings = plan(llm_fn=PlannerLLM(sensitive={"football"}), seed=1)
        self.assertNotIn("football", {s.topic for s in p.sessions})

    def test_gives_up_when_nothing_can_be_planned(self):
        with self.assertRaises(GenerationError):
            plan(llm_fn=PlannerLLM(sensitive={"Topic"}))


class PlanStoreTest(unittest.TestCase):
    def test_save_load_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            p, _ = plan()
            self.assertFalse(store.exists(p.persona_id, DAY, directory))
            path = store.save(p, directory)
            self.assertEqual(path.parent.name, "2026-01-15")
            self.assertTrue(store.exists(p.persona_id, DAY, directory))
            self.assertEqual(store.load(p.persona_id, DAY, directory), p)
            self.assertNotIn("\\u", path.read_text(encoding="utf-8"))  # Armenian/Russian stored readable


if __name__ == "__main__":
    unittest.main()
