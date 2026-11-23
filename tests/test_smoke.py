"""Small stdlib smoke tests for Relikt.

Run with: python -m unittest discover -s tests
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from eos_blocks import MAX_BLOCKS, STUB, find_blocks, parse_header  # noqa: E402


class SmokeTests(unittest.TestCase):
    def test_python_sources_parse(self):
        files = [ROOT / "relikt.py", *sorted((ROOT / "tools").rglob("*.py"))]
        self.assertTrue(files)
        for path in files:
            with self.subTest(path=path):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_truncated_block_header_is_safe(self):
        # STUB + magic only: not enough bytes for the 8-byte DIET header.
        short = STUB + b"EOS"
        self.assertIsNone(parse_header(short, 0))
        self.assertEqual(find_blocks(short), [])
        # STUB + magic + 7 of the 8 header bytes.
        self.assertIsNone(parse_header(STUB + b"EOS" + b"\x00" * 7, 0))
        self.assertEqual(find_blocks(STUB + b"EOS" + b"\x00" * 7), [])

    def test_block_limit(self):
        data = (STUB + b"EOS" + b"\x00" * 8) * (MAX_BLOCKS + 1)
        with self.assertRaises(ValueError):
            find_blocks(data)

    def _run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROOT / "relikt.py"), *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )

    def test_version_and_list(self):
        for args in (("version",), ("list",)):
            with self.subTest(args=args):
                proc = self._run_cli(*args)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    @unittest.skipUnless(
        (ROOT / "data" / "dragon5" / "DRAGON.EXE").is_file(),
        "source binary not present",
    )
    def test_verify_and_status(self):
        proc = self._run_cli("verify", "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = json.loads(proc.stdout)
        self.assertTrue(report["summary"]["passed"])

        proc = self._run_cli("status", "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["complete"])


if __name__ == "__main__":
    unittest.main()
