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


class BrowseTest(unittest.TestCase):
    PAGE = "https://www.recipes.am/salads/greek"

    def test_depth_grows_with_patience_and_is_bounded(self):
        patient = [human.browse_depth(0.9, random.Random(s)) for s in range(300)]
        hasty = [human.browse_depth(0.1, random.Random(s)) for s in range(300)]
        self.assertGreater(sum(patient), 2 * sum(hasty))
        self.assertLessEqual(max(patient), human.MAX_BROWSE_DEPTH)
        self.assertIn(0, hasty)

    def choose(self, links, visited=()):
        return human.choose_internal_link(links, self.PAGE, set(visited), random.Random(1))

    def test_only_article_links_on_the_same_site(self):
        links = [
            ("https://other.com/a-long-article-title", "A long article on another site"),
            ("https://www.recipes.am/login", "Log in to your account now"),
            ("https://www.recipes.am/menu.pdf", "Download the whole menu here"),
            ("https://www.recipes.am/", "Back to the home page please"),
            ("https://www.recipes.am/salads/greek", "This same page once more"),
            ("https://www.recipes.am/soups", "Soups"),  # menu-like, one word
            ("mailto:chef@recipes.am", "Write to the chef today"),
            ("https://news.recipes.am/2026/winter-salads", "Ten winter salads to try"),
        ]
        self.assertEqual(self.choose(links), 7)  # a subdomain of the same site is fine

    def test_same_page_with_other_slash_or_query_is_not_a_new_page(self):
        links = [("https://recipes.am/salads/greek/?ref=menu", "The same Greek salad page again"),
                 ("https://www.recipes.am/salads/caesar", "How to make a Caesar salad")]
        self.assertEqual(self.choose(links), 1)
        self.assertIsNone(self.choose(links[1:], visited={"https://recipes.am/salads/caesar/#top"}))

    def test_no_browsing_inside_social_sites(self):
        self.assertFalse(human.can_browse("https://www.facebook.com/someshop/"))
        self.assertFalse(human.can_browse("https://m.vk.com/club1"))
        self.assertTrue(human.can_browse("https://hy.wikipedia.org/wiki/Gyumri"))

    def test_skips_visited_and_returns_none_when_nothing_fits(self):
        links = [("https://www.recipes.am/salads/caesar", "How to make a Caesar salad")]
        self.assertEqual(self.choose(links), 0)
        self.assertIsNone(self.choose(links, visited={links[0][0]}))
        self.assertIsNone(self.choose([]))


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

    def test_recaptcha_on_an_ordinary_page_is_not_a_bot_check(self):
        article = "Birds of Armenia. " + "The lammergeier nests in the gorges of Syunik. " * 60
        self.assertFalse(human.is_blocked(article + " Contact us. This site is protected by reCAPTCHA."))
        self.assertTrue(human.is_blocked(article + " Our systems have detected unusual traffic"))  # strong sign
        self.assertTrue(human.is_blocked("Checking your browser... I'm not a robot"))  # short page

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
