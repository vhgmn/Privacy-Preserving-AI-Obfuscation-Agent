import json
import tempfile
import unittest
from pathlib import Path

from agent.browser.thirdparty import ThirdParties
from agent.evaluation import trackers


class RecordTest(unittest.TestCase):
    def test_third_parties_per_site(self):
        tp = ThirdParties()
        tp.record("https://www.google-analytics.com/early.js", False)  # before any page: ignored
        tp.record("https://www.news.am/article", True)
        tp.record("https://static.news.am/style.css", False)  # same site
        tp.record("https://www.google-analytics.com/g.js", False)
        tp.record("data:image/png;base64,AAAA", False)
        tp.record("https://shop.am/fridges", True)
        tp.record("https://region1.google-analytics.com/collect", False)
        tp.record("https://connect.facebook.net/sdk.js", False)
        self.assertEqual(tp.summary(), {"facebook.net": ["shop.am"],
                                        "google-analytics.com": ["news.am", "shop.am"]})


def log(domains):
    return {"time": "2026-01-15T10:00:00+00:00", "event": "third_parties", "domains": domains}


class ReportTest(unittest.TestCase):
    def report(self):
        return trackers.build({
            "aaaa0001": [log({"ga.com": ["a.am", "b.am"], "cdn.com": ["a.am"]}), log({"ga.com": ["c.am"]}),
                         {"event": "search", "query": "x"}],
            "bbbb0002": [log({"ga.com": ["d.am", "e.am"], "ads.com": ["d.am", "e.am"]})],
            "cccc0003": [],
        }, "2026-01-15", 7)

    def test_cross_site_trackers_and_shared_ones(self):
        report = self.report()
        first = report.personas["aaaa0001"]
        self.assertEqual((first["sites"], first["third_parties"]), (3, 2))
        self.assertEqual(first["cross_site"], {"ga.com": 3})
        self.assertEqual(report.shared, {"ga.com": ["aaaa0001", "bbbb0002"]})
        self.assertEqual(report.personas["cccc0003"]["sites"], 0)

    def test_report_text_and_saved_summary(self):
        report = self.report()
        text = trackers.format_report(report, {"aaaa0001": "Արամ"})
        self.assertIn("Արամ: 2 third-party domains on 3 sites", text)
        self.assertIn("followed two or more personas: 1", text)
        with tempfile.TemporaryDirectory() as tmp:
            data = json.loads(trackers.save(report, Path(tmp)).read_text(encoding="utf-8"))
        self.assertEqual(data["shared"], {"ga.com": ["aaaa0001", "bbbb0002"]})

    def test_nothing_recorded_yet(self):
        report = trackers.build({"aaaa0001": []}, "2026-01-15", 7)
        self.assertIn("Nothing recorded yet", trackers.format_report(report))


if __name__ == "__main__":
    unittest.main()
