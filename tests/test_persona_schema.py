import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent.persona import store
from agent.persona.schema import Behavior, Interest, Persona

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def make_persona(**overrides) -> Persona:
    fields = dict(
        id="0a1b2c3d",
        name="Test",
        age_group="25-34",
        occupation="architect",
        city="Yerevan",
        languages={"hy": 0.5, "ru": 0.3, "en": 0.2},
        interests=[
            Interest("houseplants", "Keeps a small balcony garden.", "long_term", 0.5, NOW),
            Interest("football", "Follows the Armenian league.", "long_term", 0.3, NOW),
            Interest("washing machine", "Choosing a new one.", "project", 0.2, NOW, NOW + timedelta(weeks=3)),
        ],
        behavior=Behavior("mixed", 240, 0.02, 0.4, 0.6),
        created_at=NOW,
    )
    fields.update(overrides)
    return Persona(**fields)


class PersonaSchemaTest(unittest.TestCase):
    def test_round_trip(self):
        persona = make_persona()
        self.assertEqual(Persona.from_dict(persona.to_dict()), persona)

    def test_rejects_invalid_values(self):
        cases = {
            "bad id": dict(id="../evil"),
            "unknown age group": dict(age_group="30"),
            "city outside Armenia": dict(city="Berlin"),
            "languages do not sum to 1": dict(languages={"hy": 0.5, "en": 0.2}),
            "unknown language": dict(languages={"de": 1.0}),
            "too few interests": dict(interests=[]),
        }
        for label, override in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                make_persona(**override)

    def test_rejects_invalid_interests(self):
        cases = {
            "weight above 1": ("x", "d", "long_term", 1.5, NOW, None),
            "zero weight": ("x", "d", "long_term", 0, NOW, None),
            "unknown kind": ("x", "d", "hobby", 0.5, NOW, None),
            "project without expiry": ("x", "d", "project", 0.5, NOW, None),
            "long_term with expiry": ("x", "d", "long_term", 0.5, NOW, NOW + timedelta(days=1)),
            "naive datetime": ("x", "d", "long_term", 0.5, NOW.replace(tzinfo=None), None),
            "empty topic": (" ", "d", "long_term", 0.5, NOW, None),
        }
        for label, args in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                Interest(*args)

    def test_rejects_duplicate_topics(self):
        base = make_persona().interests
        dup = Interest("Football ", "Again.", "long_term", 0.1, NOW)
        with self.assertRaises(ValueError):
            make_persona(interests=base + [dup])

    def test_rejects_invalid_behavior(self):
        cases = {
            "wpm as float": ("mixed", 240.0, 0.02, 0.4, 0.6),
            "wpm too fast": ("mixed", 900, 0.02, 0.4, 0.6),
            "typo rate too high": ("mixed", 240, 0.5, 0.4, 0.6),
            "unknown query style": ("emoji", 240, 0.02, 0.4, 0.6),
        }
        for label, args in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                Behavior(*args)

    def test_from_dict_rejects_unknown_fields(self):
        data = make_persona().to_dict()
        data["real_name"] = "leak"
        with self.assertRaises(ValueError):
            Persona.from_dict(data)

    def test_from_dict_rejects_wrong_schema_version(self):
        data = make_persona().to_dict()
        data["schema_version"] = 99
        with self.assertRaises(ValueError):
            Persona.from_dict(data)


class PersonaStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_load_list_delete(self):
        persona = make_persona(name="Արմեն")
        path = store.save(persona, self.dir)
        self.assertIn("Արմեն", path.read_text(encoding="utf-8"))  # stored readable, not \u-escaped
        self.assertEqual(store.load(persona.id, self.dir), persona)
        self.assertEqual(store.list_all(self.dir), [persona])
        self.assertEqual(list(self.dir.glob("*.tmp")), [])
        store.delete(persona.id, self.dir)
        self.assertEqual(store.list_all(self.dir), [])

    def test_list_all_on_missing_directory(self):
        self.assertEqual(store.list_all(self.dir / "missing"), [])

    def test_rejects_path_traversal_id(self):
        with self.assertRaises(ValueError):
            store.load("../../secrets", self.dir)

    def test_corrupt_file_names_the_file(self):
        (self.dir / "deadbeef.json").write_text("{not json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "deadbeef.json"):
            store.load("deadbeef", self.dir)


if __name__ == "__main__":
    unittest.main()
