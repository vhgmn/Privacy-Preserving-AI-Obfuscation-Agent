import random
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from agent.evaluation import classifier

DAY = date(2026, 1, 15)
T0 = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)


def chrome_time(when: datetime) -> int:
    return int((when - datetime(1601, 1, 1, tzinfo=timezone.utc)).total_seconds() * 1_000_000)


def fake_history(path: Path, rows) -> None:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    db.execute("CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER, transition INTEGER)")
    for i, (when, url, transition) in enumerate(rows, 1):
        db.execute("INSERT INTO urls VALUES (?, ?, 'secret title')", (i, url))
        db.execute("INSERT INTO visits (url, visit_time, transition) VALUES (?, ?, ?)", (i, chrome_time(when), transition))
    db.commit()
    db.close()


class HistoryTest(unittest.TestCase):
    def test_reads_only_time_and_domain_of_pages_opened(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "History"
            fake_history(path, [
                (T0, "https://www.github.com/me/private-repo?token=x", 0x30000001),  # typed, with qualifiers
                (T0 + timedelta(seconds=5), "https://ads.example.com/frame", 3),  # subframe: skipped
                (T0 + timedelta(seconds=9), "chrome://settings", 1),  # not a web page
                (T0 + timedelta(days=3), "https://news.am/x", 0),  # outside the window
            ])
            visits = classifier.read_history(T0 - timedelta(hours=1), T0 + timedelta(days=1), path)
            self.assertEqual([name for _, name in visits], ["github.com"])
            self.assertEqual(visits[0][0], T0.astimezone())
            self.assertTrue(path.exists())  # the original is never touched


class SessionTest(unittest.TestCase):
    def test_decoy_visits_from_log_events(self):
        events = [
            {"time": "2026-01-15T10:00:00+00:00", "event": "session_start", "topic": "secret"},
            {"time": "2026-01-15T10:00:05+00:00", "event": "search", "query": "q"},
            {"time": "2026-01-15T10:00:09+00:00", "event": "open_result", "url": "https://www.shop.am/a"},
            {"time": "2026-01-15T10:02:00+00:00", "event": "read", "url": "https://www.shop.am/a"},
            {"time": "2026-01-15T10:05:00+00:00", "event": "youtube", "query": "q", "url": "https://youtube.com/w"},
        ]
        self.assertEqual([n for _, n in classifier.decoy_visits(events)], ["duckduckgo.com", "shop.am", "youtube.com"])

    def test_sessions_split_on_long_pauses_and_merge_reloads(self):
        visits = [(T0, "a.com"), (T0 + timedelta(seconds=1), "a.com"), (T0 + timedelta(minutes=10), "b.com"),
                  (T0 + timedelta(hours=2), "a.com")]
        groups = classifier.sessions(list(reversed(visits)))
        self.assertEqual([[n for _, n in g] for g in groups], [["a.com", "b.com"], ["a.com"]])

    def test_features(self):
        groups = [[(T0, "duckduckgo.com"), (T0 + timedelta(minutes=1), "shop.am"),
                   (T0 + timedelta(minutes=3), "youtube.com")],
                  [(T0 + timedelta(hours=3), "shop.am")]]
        rows = classifier.features(groups)
        first = dict(zip(classifier.FEATURES, rows[0]))
        self.assertEqual((first["minutes"], first["pages"], first["domains"]), (3, 3, 3))
        self.assertEqual(first["gap_median_s"], 90)
        self.assertAlmostEqual(first["search_share"], 1 / 3)
        self.assertEqual(first["new_domain_share"], 1.0)
        self.assertEqual(dict(zip(classifier.FEATURES, rows[1]))["new_domain_share"], 0.0)  # seen before
        self.assertEqual(len(rows[0]), len(classifier.FEATURES))


class EvaluateTest(unittest.TestCase):
    def rows(self, rng, shift):
        return [[rng.gauss(shift, 1) for _ in classifier.FEATURES] for _ in range(30)]

    def test_too_few_sessions_shows_only_averages(self):
        rng = random.Random(1)
        result = classifier.evaluate(self.rows(rng, 0), self.rows(rng, 0)[:3], DAY, 7)
        self.assertIsNone(result.auc)
        self.assertIn("too few sessions", classifier.format_report(result))

    def test_separable_data_gives_high_auc_and_names_the_feature(self):
        rng = random.Random(2)
        real, decoy = self.rows(rng, 0), self.rows(rng, 0)
        index = classifier.FEATURES.index("new_domain_share")
        for row in decoy:
            row[index] += 5
        result = classifier.evaluate(real, decoy, DAY, 7)
        self.assertGreater(result.auc["random forest"], 0.9)
        self.assertEqual(next(iter(result.importance)), "new_domain_share")
        self.assertIn("gives the decoys away", classifier.format_report(result))

    def test_identical_sources_are_near_chance(self):
        rng = random.Random(3)
        result = classifier.evaluate(self.rows(rng, 0), self.rows(rng, 0), DAY, 7)
        self.assertLess(result.auc["logistic regression"], 0.75)

    def test_saved_summary_has_no_visits(self):
        rng = random.Random(4)
        result = classifier.evaluate(self.rows(rng, 0), self.rows(rng, 0), DAY, 7)
        with tempfile.TemporaryDirectory() as tmp:
            text = classifier.save(result, Path(tmp)).read_text(encoding="utf-8")
        self.assertIn('"auc"', text)
        self.assertNotIn(".com", text)


if __name__ == "__main__":
    unittest.main()
