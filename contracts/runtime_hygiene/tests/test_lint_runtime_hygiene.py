from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from contracts.runtime_hygiene.lint_runtime_hygiene import TEMPLATE_DIR, check_source, lint, main


class LoggerNamespaceTests(unittest.TestCase):
    def test_cog_logger_and_child_pass(self) -> None:
        source = (
            "import logging\n"
            'a = logging.getLogger("red.pixel-agents-cogs.pico")\n'
            'b = logging.getLogger("red.pixel-agents-cogs.pico.listener")\n'
            'c = logging.getLogger(f"red.pixel-agents-cogs.{__name__}")\n'
        )
        self.assertEqual(check_source(source, "pico", "pico/x.py"), [])

    def test_old_names_fail(self) -> None:
        for name in (
            '"red.pico"',
            '"red.d_cogs.cctv"',
            '"red.office-cogs.switchboard"',
            "__name__",
            '"red.pixel-agents-cogs.picox"',
            '"red.pixel-agents-cogs.architect"',
            'f"red.{__name__}"',
            "",
        ):
            with self.subTest(name=name):
                source = f"import logging\nlog = logging.getLogger({name})\n"
                problems = check_source(source, "pico", "pico/x.py")
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("pico/x.py:2: logger", problems[0])

    def test_bare_getlogger_import_is_checked(self) -> None:
        source = 'from logging import getLogger\nlog = getLogger("red.pico")\n'
        self.assertEqual(len(check_source(source, "pico", "p.py")), 1)


class BlockingCallTests(unittest.TestCase):
    def test_blocking_call_in_coroutine_fails(self) -> None:
        source = (
            "import subprocess, time\n"
            "async def build(path):\n"
            "    subprocess.run(['npm'])\n"
            "    time.sleep(1)\n"
            "    path.read_text()\n"
        )
        problems = check_source(source, "cog", "cog/x.py")
        self.assertEqual([p.split(":")[1] for p in problems], ["3", "4", "5"], problems)
        self.assertIn("async def build", problems[0])

    def test_sync_helper_and_nested_def_pass(self) -> None:
        source = (
            "import asyncio, subprocess\n"
            "def build():\n"
            "    subprocess.run(['npm'])\n"
            "async def outer():\n"
            "    def inner():\n"
            "        subprocess.run(['npm'])\n"
            "    await asyncio.to_thread(inner)\n"
            "    await asyncio.to_thread(lambda: subprocess.run(['npm']))\n"
        )
        self.assertEqual(check_source(source, "cog", "cog/x.py"), [])


class LintTreeTests(unittest.TestCase):
    def _tree(self, root: Path, body: str, *, test_body: str = "") -> None:
        cog = root / "newcog"
        (cog / "tests").mkdir(parents=True)
        (cog / "info.json").write_text(json.dumps({"type": "COG"}))
        (cog / "x.py").write_text(body)
        (cog / "tests" / "test_x.py").write_text(test_body)
        shared = root / "sharedlib"
        shared.mkdir()
        (shared / "info.json").write_text(json.dumps({"type": "SHARED_LIBRARY"}))
        (shared / "y.py").write_text('import logging\nlogging.getLogger("anything")\n')
        namespace = root / "contracts"
        namespace.mkdir()
        (namespace / "z.py").write_text('import logging\nlogging.getLogger("anything")\n')

    def test_tests_and_shared_libraries_are_skipped(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tree(
                root,
                'import logging\nlogging.getLogger("red.pixel-agents-cogs.newcog")\n',
                test_body='import logging\nlogging.getLogger("test")\n',
            )
            self.assertEqual(lint(root), [])

    def test_bad_logger_in_cog_reported(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tree(root, 'import logging\nlogging.getLogger("red.newcog")\n')
            self.assertEqual(len(lint(root)), 1)

    def test_template_rendered_before_check(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tree(root, "")
            template = root / TEMPLATE_DIR
            template.mkdir(parents=True)
            good = 'import logging\nlogging.getLogger("red.pixel-agents-cogs.{{cookiecutter.cog_name}}")\n'
            (template / "a.py").write_text(good)
            self.assertEqual(lint(root), [])
            (template / "a.py").write_text(
                'import logging\nlogging.getLogger("red.{{ cookiecutter.cog_name }}")\n'
            )
            self.assertEqual(len(lint(root)), 1)

    def test_repo_is_clean(self) -> None:
        self.assertEqual(main([]), 0)


if __name__ == "__main__":
    unittest.main()
