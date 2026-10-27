import contextlib
import dataclasses
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent import config
from agent.persona import store
from agent.persona.__main__ import main
from agent.persona.generator import GenerationError
from tests.test_persona_schema import make_persona


class PersonaCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(config, "PERSONAS_DIR", Path(self._tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_generate_fills_up_to_target_and_saves(self):
        ids = iter(["00000001", "00000002", "00000003", "00000004"])
        fake = lambda existing: make_persona(id=next(ids), occupation=f"job {len(existing)}")
        with mock.patch("agent.persona.__main__.generate_persona", side_effect=fake):
            code, out, _ = self.run_cli("generate")
            self.assertEqual(code, 0)
            self.assertEqual(len(store.list_all()), config.PERSONA_COUNT)
            code, out, _ = self.run_cli("generate")
            self.assertIn("Already have", out)

    def test_list_show_delete(self):
        store.save(make_persona())
        self.assertIn("0a1b2c3d", self.run_cli("list")[1])
        self.assertIn('"occupation": "architect"', self.run_cli("show", "0a1b2c3d")[1])
        self.assertEqual(self.run_cli("delete", "0a1b2c3d")[0], 0)
        self.assertIn("No personas yet", self.run_cli("list")[1])

    def test_refresh_saves_updates_and_keeps_failures_unchanged(self):
        ok, broken = make_persona(id="00000001"), make_persona(id="00000002", occupation="pilot")
        store.save(ok)
        store.save(broken)
        before = (Path(self._tmp.name) / "00000002.json").read_text(encoding="utf-8")

        def fake_refresh(persona, others):
            if persona.id == "00000002":
                raise GenerationError("model gave up")
            return dataclasses.replace(persona, occupation="baker")

        with mock.patch("agent.persona.__main__.refresh_persona", side_effect=fake_refresh):
            code, out, err = self.run_cli("refresh")
        self.assertEqual(code, 1)
        self.assertIn("left unchanged", err)
        self.assertEqual(store.load("00000001").occupation, "baker")
        self.assertEqual((Path(self._tmp.name) / "00000002.json").read_text(encoding="utf-8"), before)

    def test_show_unknown_id_is_an_error(self):
        code, _, err = self.run_cli("show", "ffffffff")
        self.assertEqual(code, 1)
        self.assertIn("error", err)


if __name__ == "__main__":
    unittest.main()
