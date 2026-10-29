import json
import math
import random
import tempfile
import unittest
from collections import Counter
from datetime import date
from pathlib import Path

from agent.browser import human
from agent.browser.runner import SessionLog
from agent.browser.sites import choose_result


def replay(actions):
    """What ends up in the search box after the keystrokes."""
    text = []
    for action, char, _ in actions:
        if action == "type":
            text.append(char)
        elif action == "backspace":
            text.pop()
    return "".join(text)


class TypingTest(unittest.TestCase):
    def test_typos_are_always_corrected(self):
        for text in ("fridge prices", "սառնարանի գները", "холодильник LG цена"):
            for seed in range(30):
                actions = human.typing_actions(text, 0.3, random.Random(seed))
                self.assertEqual(replay(actions), text)

    def test_typos_happen_and_stay_in_the_same_alphabet(self):
        actions = human.typing_actions("սառնարանի գները", 0.5, random.Random(1))
        self.assertTrue(any(a == "backspace" for a, _, _ in actions))
        typed = {c for a, c, _ in actions if a == "type" and c.isalpha()}
        self.assertTrue(all("Ա" <= c <= "և" for c in typed))

    def test_no_typos_at_zero_rate(self):
        actions = human.typing_actions("chess openings", 0.0, random.Random(1))
        self.assertFalse(any(a == "backspace" for a, _, _ in actions))

    def test_delays_are_human(self):
        delays = [human.key_delay(random.Random(s)) for s in range(200)]
        self.assertTrue(all(0.04 <= d <= 1.2 for d in delays))
        self.assertGreater(len(set(round(d, 3) for d in delays)), 100)  # not a fixed rhythm


class TimingTest(unittest.TestCase):
    def test_read_seconds_bounds_and_patience(self):
        short = [human.read_seconds(10, 240, 0.5, random.Random(s)) for s in range(50)]
        long = [human.read_seconds(100_000, 240, 1.0, random.Random(s)) for s in range(50)]
        self.assertTrue(all(human.MIN_READ_SECONDS <= r <= 2 * human.MIN_READ_SECONDS for r in short))
        self.assertTrue(all(0.75 * human.USUAL_MAX_READ_SECONDS <= r <= human.MAX_READ_SECONDS for r in long))
        # no single limit that many reads end at
        self.assertLessEqual(max(Counter(round(r) for r in long).values()), 3)
        self.assertGreater(len({round(r) for r in short}), 10)
        patient = sum(human.read_seconds(1500, 240, 0.9, random.Random(s)) for s in range(50))
        hasty = sum(human.read_seconds(1500, 240, 0.1, random.Random(s)) for s in range(50))
        self.assertGreater(patient, hasty)

    def test_watch_seconds_bounds(self):
        for seed in range(50):
            self.assertTrue(30 <= human.watch_seconds(seed / 50, random.Random(seed)) <= 180)

    def test_scroll_plan_fits_time_and_mostly_goes_down(self):
        plan = human.scroll_plan(120, random.Random(2))
        self.assertLessEqual(sum(p for p, _ in plan), 120)
        self.assertGreater(sum(1 for _, px in plan if px > 0), sum(1 for _, px in plan if px < 0))


class MouseTest(unittest.TestCase):
    def test_path_ends_on_target_and_is_curved(self):
        path = human.mouse_path((0, 0), (400, 0), random.Random(5))
        self.assertEqual(path[-1], (400, 0))
        self.assertTrue(any(abs(y) > 1 for _, y in path))  # not a straight line
        steps = [math.dist(a, b) for a, b in zip(path, path[1:])]
        self.assertLess(max(steps), 100)  # no teleporting


class DetectionTest(unittest.TestCase):
    def test_bot_checks_are_recognised(self):
        for text in ("Please complete the CAPTCHA", "Our systems have detected unusual traffic",
                     "Unfortunately, bots use DuckDuckGo too", "Подтвердите, что вы не робот"):
            self.assertTrue(human.is_blocked(text), text)
        self.assertFalse(human.is_blocked("Best fridges of 2026 reviewed"))

    def test_only_reject_buttons_are_clicked(self):
        for label in ("Reject all", "Decline", "Отклонить", " Մերժել "):
            self.assertTrue(human.is_reject_button(label), label)
        for label in ("Accept all", "Accept", "I agree", "Reject all and subscribe", "Принять"):
            self.assertFalse(human.is_reject_button(label), label)


class ChooseResultTest(unittest.TestCase):
    HREFS = ["https://a", "https://b", "https://c"]

    def test_picks_the_planned_rank(self):
        self.assertEqual(choose_result(self.HREFS, 2, set()), 1)

    def test_rank_beyond_results_takes_the_last(self):
        self.assertEqual(choose_result(self.HREFS, 5, set()), 2)

    def test_skips_pages_already_read(self):
        self.assertEqual(choose_result(self.HREFS, 1, {"https://a"}), 1)
        self.assertEqual(choose_result(self.HREFS, 3, {"https://c"}), 1)  # nothing below: goes up
        self.assertIsNone(choose_result(self.HREFS, 1, set(self.HREFS)))
        self.assertIsNone(choose_result([], 1, set()))


class SessionLogTest(unittest.TestCase):
    def test_appends_json_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = SessionLog("0a1b2c3d", date(2026, 1, 15), 2, Path(tmp))
            log.write("search", query="սառնարան")
            log.write("read", url="https://example.com", seconds=42)
            lines = [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([l["event"] for l in lines], ["search", "read"])
        self.assertEqual(lines[0]["query"], "սառնարան")
        self.assertEqual(lines[1]["session"], 2)


if __name__ == "__main__":
    unittest.main()
