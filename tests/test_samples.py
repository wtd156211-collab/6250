"""Byte-exact trace comparison against samples/expected, plus determinism."""

import hashlib
import unittest
from pathlib import Path

from snapshotdb.runner import load_script, run_script

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "samples" / "scripts"
EXPECTED = ROOT / "samples" / "expected"


def script_paths():
    return sorted(SCRIPTS.glob("*.json"), key=lambda p: p.name)


class SampleTraceTests(unittest.TestCase):
    def test_scripts_exist(self):
        self.assertEqual(len(script_paths()), 6)

    def test_traces_match_expected_byte_for_byte(self):
        for path in script_paths():
            with self.subTest(script=path.name):
                got = run_script(load_script(path)).encode("ascii")
                want = (EXPECTED / (path.stem + ".txt")).read_bytes()
                self.assertEqual(got, want)

    def test_same_script_twice_same_sha256(self):
        for path in script_paths():
            with self.subTest(script=path.name):
                script = load_script(path)
                first = hashlib.sha256(run_script(script).encode("ascii")).hexdigest()
                second = hashlib.sha256(run_script(script).encode("ascii")).hexdigest()
                self.assertEqual(first, second)

    def test_trace_is_pure_ascii_with_final_newline(self):
        for path in script_paths():
            with self.subTest(script=path.name):
                text = run_script(load_script(path))
                text.encode("ascii")
                self.assertTrue(text.endswith("\n"))
                self.assertNotIn("\r", text)


if __name__ == "__main__":
    unittest.main()
