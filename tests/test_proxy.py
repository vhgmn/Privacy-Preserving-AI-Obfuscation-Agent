import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent import config, proxy
from agent.browser import sites


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ConfigTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "proxies.json"

    def test_no_file_means_direct(self):
        self.assertIsNone(proxy.for_persona("0a1b2c3d", self.path))
        self.assertIsNone(proxy.required("0a1b2c3d", self.path))

    def test_own_entry_wins_over_default(self):
        proxy.save({"default": "socks5://127.0.0.1:1080", "0a1b2c3d": "socks5://127.0.0.1:1081"}, self.path)
        self.assertEqual(proxy.for_persona("0a1b2c3d", self.path), "socks5://127.0.0.1:1081")
        self.assertEqual(proxy.for_persona("ffffffff", self.path), "socks5://127.0.0.1:1080")

    def test_only_socks5_with_a_port(self):
        for bad in ("http://127.0.0.1:8080", "socks5://127.0.0.1", "127.0.0.1:1080"):
            with self.assertRaises(ValueError):
                proxy.check_url(bad)
        self.assertEqual(proxy.check_url("socks5://127.0.0.1:1080"), ("127.0.0.1", 1080))


class FailClosedTest(unittest.TestCase):
    def test_silent_proxy_stops_the_persona(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proxies.json"
            proxy.save({"default": f"socks5://127.0.0.1:{free_port()}"}, path)
            with self.assertRaises(proxy.ProxyDown):
                proxy.required("0a1b2c3d", path)

    def test_listening_proxy_is_used(self):
        with socket.socket() as server, tempfile.TemporaryDirectory() as tmp:
            server.bind(("127.0.0.1", 0))
            server.listen()
            url = f"socks5://127.0.0.1:{server.getsockname()[1]}"
            path = Path(tmp) / "proxies.json"
            proxy.save({"0a1b2c3d": url}, path)
            self.assertEqual(proxy.required("0a1b2c3d", path), url)

    def test_browser_is_not_started_without_its_proxy(self):
        from agent.browser import runner
        from tests.test_persona_schema import make_persona

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(config, "DATA_DIR", Path(tmp)), \
                mock.patch.object(runner, "sync_playwright") as playwright:
            proxy.save({"default": f"socks5://127.0.0.1:{free_port()}"})
            with self.assertRaises(proxy.ProxyDown):
                with runner.persona_browser(make_persona(), echo=lambda _: None):
                    pass
            playwright.assert_not_called()


class SlowNetworkTest(unittest.TestCase):
    def test_timeouts_scale(self):
        self.addCleanup(setattr, sites, "SLOW_FACTOR", 1.0)
        sites.SLOW_FACTOR = 3.0
        self.assertEqual(sites._ms(), 3 * sites.LOAD_TIMEOUT_MS)


if __name__ == "__main__":
    unittest.main()
