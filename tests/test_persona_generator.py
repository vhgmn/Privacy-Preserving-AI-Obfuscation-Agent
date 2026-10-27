import json
import random
import unittest
from datetime import datetime, timedelta, timezone

from agent import config
from agent.persona.generator import GenerationError, generate_persona, refresh_persona
from agent.persona.names import FEMALE_NAMES, MALE_NAMES
from agent.persona.schema import MAX_PAST_PROJECTS
from tests.test_persona_schema import make_persona

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)

IDENTITY = {"occupation": "baker", "query_style": "mixed", "bio": "Bakes bread. Likes walks."}
INTERESTS = {
    "long_term": [
        {"topic": "chess", "description": "Plays online."},
        {"topic": "hiking", "description": "Weekend trails."},
        {"topic": "jazz", "description": "Collects records."},
        {"topic": "photography", "description": "Street photos."},
    ],
    "projects": [
        {"topic": "new laptop", "description": "Comparing models."},
        {"topic": "trip to Tbilisi", "description": "Planning a weekend."},
    ],
}


class FakeLLM:
    """Answers each prompt type from a queue; the last answer repeats once the queue runs out."""

    def __init__(self, identity=None, interests=None, sensitive=()):
        self.queues = {
            "identity": list(identity or [json.dumps(IDENTITY, ensure_ascii=False)]),
            "interests": list(interests or [json.dumps(INTERESTS)]),
        }
        self.sensitive = set(sensitive)
        self.replacements = 0
        self.calls = []

    def _next(self, name):
        queue = self.queues[name]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def __call__(self, prompt, json_mode=False, options=None, **_):
        self.calls.append((prompt, options))
        if prompt.startswith("Does this topic relate"):
            topic_line = next(line for line in prompt.splitlines() if line.startswith("Topic: "))
            flagged = any(word in topic_line for word in self.sensitive)
            return json.dumps({"sensitive": flagged})
        if prompt.startswith("You are inventing a fictional"):
            return self._next("identity")
        if "Suggest one more thing" in prompt:
            self.replacements += 1
            return json.dumps({"topic": f"backup topic {self.replacements}", "description": "Fallback."})
        return self._next("interests")


def generate(llm_fn, existing=()):
    return generate_persona(existing, llm_fn=llm_fn, rng=random.Random(7), now=NOW)


class GeneratorTest(unittest.TestCase):
    def test_happy_path(self):
        fake = FakeLLM()
        persona = generate(fake)
        self.assertIn(persona.name, MALE_NAMES + FEMALE_NAMES)
        self.assertFalse(any(persona.name in prompt for prompt, _ in fake.calls))  # never sent to the LLM
        self.assertEqual(persona.occupation, "baker")
        self.assertTrue(any(i.kind == "project" for i in persona.interests))
        self.assertTrue(all(i.expires_at for i in persona.interests if i.kind == "project"))
        self.assertAlmostEqual(sum(persona.languages.values()), 1.0, places=3)
        self.assertEqual(fake.replacements, 0)
        safety_options = [opts for prompt, opts in fake.calls if prompt.startswith("Does this topic relate")]
        self.assertTrue(safety_options)
        self.assertTrue(all(opts == {"temperature": 0} for opts in safety_options))

    def test_sensitive_topic_is_replaced(self):
        fake = FakeLLM(sensitive={"chess"})
        topics = {i.topic for i in generate(fake).interests}
        self.assertNotIn("chess", topics)
        self.assertIn("backup topic 1", topics)
        # the replacement stays in the same area as the rejected topic
        interests_prompt = next(p for p, _ in fake.calls if p.startswith("Person:") and "For each" in p)
        replacement_prompt = next(p for p, _ in fake.calls if "Suggest one more" in p)
        first_area = interests_prompt.split("they have: ")[1].split(";")[0].split(".\n")[0]
        self.assertIn(f'"{first_area}"', replacement_prompt)

    def test_areas_come_from_config(self):
        fake = FakeLLM()
        generate(fake)
        prompt = next(p for p, _ in fake.calls if p.startswith("Person:") and "For each" in p)
        self.assertTrue(any(c in prompt for c in config.INTEREST_CATEGORIES))
        self.assertTrue(any(c in prompt for c in config.PROJECT_CATEGORIES))

    def test_topic_that_repeats_an_area_name_is_replaced(self):
        interests = json.loads(json.dumps(INTERESTS))
        interests["long_term"][0]["topic"] = "Cooking and Food"
        topics = {i.topic for i in generate(FakeLLM(interests=[json.dumps(interests)])).interests}
        self.assertNotIn("Cooking and Food", topics)

    def test_unclear_safety_answer_counts_as_sensitive(self):
        fake = FakeLLM()
        original = fake.__call__

        def garbled(prompt, **kwargs):
            if prompt.startswith("Does this topic relate") and "chess" in prompt:
                return "not json"
            return original(prompt, **kwargs)

        topics = {i.topic for i in generate(garbled).interests}
        self.assertNotIn("chess", topics)

    def test_topic_of_existing_persona_is_replaced(self):
        other = make_persona()  # has "football", "houseplants", "washing machine"
        interests = json.loads(json.dumps(INTERESTS))
        interests["long_term"][0]["topic"] = "Football"
        persona = generate(FakeLLM(interests=[json.dumps(interests)]), existing=[other])
        self.assertNotIn("football", {i.topic.casefold() for i in persona.interests})

    def test_name_differs_from_existing_personas(self):
        first = generate(FakeLLM())
        other = make_persona(name=first.name, occupation="pilot")
        self.assertNotEqual(generate(FakeLLM(), existing=[other]).name, first.name)

    def test_retries_after_bad_json(self):
        fake = FakeLLM(identity=["{broken", json.dumps({"occupation": "x"}), json.dumps(IDENTITY)])
        self.assertEqual(generate(fake).occupation, "baker")

    def test_rejects_taken_occupation(self):
        other = make_persona(occupation="Baker")
        with self.assertRaisesRegex(GenerationError, "identity"):
            generate(FakeLLM(), existing=[other])

    def test_gives_up_after_max_attempts(self):
        with self.assertRaisesRegex(GenerationError, "identity: gave up after 3 attempts"):
            generate(FakeLLM(identity=["{broken"]))

    def test_missing_items_are_filled_by_replacement(self):
        too_few = {"long_term": INTERESTS["long_term"][:1], "projects": INTERESTS["projects"]}
        fake = FakeLLM(interests=[json.dumps(too_few)])
        persona = generate(fake)
        self.assertIn("chess", {i.topic for i in persona.interests})
        self.assertGreater(fake.replacements, 0)
        self.assertGreaterEqual(len(persona.interests), 3)

    def test_gives_up_when_no_usable_items(self):
        empty = {"long_term": [], "projects": []}
        with self.assertRaisesRegex(GenerationError, "no usable items"):
            generate(FakeLLM(interests=[json.dumps(empty)]))

    def test_gives_up_when_projects_list_missing(self):
        no_projects = {"long_term": INTERESTS["long_term"]}
        with self.assertRaisesRegex(GenerationError, "projects"):
            generate(FakeLLM(interests=[json.dumps(no_projects)]))


class RefreshTest(unittest.TestCase):
    LATER = NOW + timedelta(weeks=4)  # make_persona's "washing machine" project ends after 3 weeks

    def refresh(self, persona, fake, now=LATER, others=()):
        return refresh_persona(persona, others, llm_fn=fake, rng=random.Random(7), now=now)

    def test_replaces_expired_project(self):
        updated = self.refresh(make_persona(), FakeLLM())
        topics = {i.topic for i in updated.interests}
        self.assertNotIn("washing machine", topics)
        self.assertIn("backup topic 1", topics)
        self.assertTrue({"houseplants", "football"} <= topics)
        self.assertEqual(updated.past_projects, ["washing machine"])
        new = next(i for i in updated.interests if i.topic == "backup topic 1")
        self.assertEqual(new.kind, "project")
        self.assertGreater(new.expires_at, self.LATER)
        self.assertAlmostEqual(sum(i.weight for i in updated.interests), 1.0, places=2)

    def test_nothing_expired_means_no_llm_calls(self):
        fake = FakeLLM()
        self.assertIsNone(self.refresh(make_persona(), fake, now=NOW))
        self.assertEqual(fake.calls, [])

    def test_past_projects_are_capped(self):
        old = [f"old project {n}" for n in range(MAX_PAST_PROJECTS)]
        updated = self.refresh(make_persona(past_projects=old), FakeLLM())
        self.assertEqual(len(updated.past_projects), MAX_PAST_PROJECTS)
        self.assertEqual(updated.past_projects[-1], "washing machine")
        self.assertNotIn("old project 0", updated.past_projects)

    def test_context_names_gender_and_finished_projects(self):
        fake = FakeLLM()
        self.refresh(make_persona(name="Անի"), fake)
        prompt = next(p for p, _ in fake.calls if "Suggest one more" in p)
        self.assertIn("A woman", prompt)
        self.assertIn("Recently finished projects: washing machine", prompt)
        self.assertNotIn("Անի", prompt)

    def test_failure_raises_and_leaves_original_untouched(self):
        persona = make_persona()
        before = persona.to_dict()
        with self.assertRaises(GenerationError):
            self.refresh(persona, FakeLLM(sensitive={"backup"}))
        self.assertEqual(persona.to_dict(), before)


if __name__ == "__main__":
    unittest.main()
