import json
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path

import httpx

from agent.browser.runner import match_user_languages
from agent.evaluation import fingerprint

DAY = date(2026, 1, 15)


def fake(**changes):
    fp = {"userAgent": "Chrome/153", "timezone": "Asia/Yerevan", "screen": "1920x1080",
          "fonts": "Arial, Calibri", "canvas": "data:image/png;base64,AAAA"}
    fp.update(changes)
    return fingerprint.normalize(fp)


class NormalizeTest(unittest.TestCase):
    def test_canvas_image_becomes_a_short_hash(self):
        fp = fake()
        self.assertNotIn("canvas", fp)
        self.assertEqual(len(fp["canvasHash"]), 16)
        self.assertNotEqual(fp["canvasHash"], fake(canvas="data:image/png;base64,BBBB")["canvasHash"])

    def test_missing_canvas(self):
        self.assertIsNone(fingerprint.normalize({"userAgent": "x"})["canvasHash"])

    def test_probe_lists_the_fonts(self):
        self.assertIn('"Sylfaen"', fingerprint.PROBE_JS)
        self.assertNotIn("FONTS", fingerprint.PROBE_JS)


class CompareTest(unittest.TestCase):
    def test_identical_profiles_score_one(self):
        comparison = fingerprint.compare({"a": fake(), "b": fake(), "c": fake()})
        self.assertEqual(comparison.score, 1.0)
        self.assertEqual(set(comparison.pairs.values()), {1.0})

    def test_differences_are_found(self):
        comparison = fingerprint.compare({"a": fake(), "b": fake(), "mine": fake(fonts="Arial, Calibri, Fira Code")})
        self.assertNotIn("fonts", comparison.same)
        self.assertEqual(comparison.score, 4 / 5)
        self.assertEqual(comparison.pairs[("a", "b")], 1.0)
        self.assertEqual(comparison.pairs[("a", "mine")], 4 / 5)

    def test_attribute_missing_in_one_profile_differs(self):
        comparison = fingerprint.compare({"a": fake(extra=1), "b": fake()})
        self.assertEqual(comparison.rows["extra"], [1, None])
        self.assertNotIn("extra", comparison.same)

    def test_report_uses_names(self):
        text = fingerprint.format_report(fingerprint.compare({"a": fake(), "mine": fake(screen="1366x768")}),
                                         {"a": "Արամ", "mine": "you"})
        self.assertIn("Արամ", text)
        self.assertIn("linkability score 0.80", text)
        self.assertIn("same IP", text)


class StoreTest(unittest.TestCase):
    def test_round_trip_per_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            fingerprint.save("a", fake(), DAY, Path(tmp))
            fingerprint.save("mine", fake(screen="1x1"), DAY, Path(tmp))
            loaded = fingerprint.load_day(DAY, Path(tmp))
            self.assertEqual(list(loaded), ["a", "mine"])
            self.assertEqual(loaded["mine"]["screen"], "1x1")
            self.assertEqual(fingerprint.load_day(date(2026, 1, 16), Path(tmp)), {})


class ServeProbeTest(unittest.TestCase):
    def run_server(self, timeout=10):
        urls, result = [], {}
        ready = threading.Event()

        def on_ready(url):
            urls.append(url)
            ready.set()

        thread = threading.Thread(target=lambda: result.update(fp=fingerprint.serve_probe(timeout, on_ready)))
        thread.start()
        self.assertTrue(ready.wait(5))
        return urls[0], thread, result

    def test_page_posts_back_and_server_stops(self):
        url, thread, result = self.run_server()
        page = httpx.get(url).text
        self.assertIn("navigator", page)
        submit = url + "/submit"
        self.assertIn(submit.split("127.0.0.1")[1].split("/", 1)[1], page)
        self.assertEqual(httpx.post(submit, content='{"screen": "1x1", "canvas": "x"}').status_code, 204)
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["fp"]["screen"], "1x1")
        self.assertIn("canvasHash", result["fp"])

    def test_wrong_token_is_refused(self):
        # long enough that the server is still up for both requests on a busy machine
        url, thread, result = self.run_server(timeout=6)
        base = url.rsplit("/", 1)[0]
        self.assertEqual(httpx.get(base + "/guess").status_code, 404)
        self.assertEqual(httpx.post(base + "/guess/submit", content="{}").status_code, 404)
        thread.join(10)
        self.assertIsNone(result["fp"])


class LatestMineTest(unittest.TestCase):
    def test_most_recent_day_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(fingerprint.latest_mine(Path(tmp)))
            fingerprint.save("mine", fake(languages="en-US,en"), DAY, Path(tmp))
            fingerprint.save("mine", fake(languages="en-GB,en"), date(2026, 2, 1), Path(tmp))
            fingerprint.save("a", fake(languages="ru"), date(2026, 3, 1), Path(tmp))
            self.assertEqual(fingerprint.latest_mine(Path(tmp))["languages"], "en-GB,en")


class MatchLanguagesTest(unittest.TestCase):
    def test_writes_languages_and_keeps_other_prefs(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefs = Path(tmp) / "Default" / "Preferences"
            prefs.parent.mkdir()
            prefs.write_text('{"intl": {"selected_languages": "en-US,en"}, "other": 1}', encoding="utf-8")
            self.assertTrue(match_user_languages(Path(tmp), "en-GB,en-US,en"))
            data = json.loads(prefs.read_text(encoding="utf-8"))
            self.assertEqual(data["intl"], {"selected_languages": "en-GB,en-US,en", "accept_languages": "en-GB,en-US,en"})
            self.assertEqual(data["other"], 1)
            self.assertFalse(match_user_languages(Path(tmp), "en-GB,en-US,en"))

    def test_new_profile_and_unknown_languages(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(match_user_languages(Path(tmp), None))
            self.assertFalse((Path(tmp) / "Default").exists())
            self.assertTrue(match_user_languages(Path(tmp), "hy,ru"))
            self.assertIn("hy,ru", (Path(tmp) / "Default" / "Preferences").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
