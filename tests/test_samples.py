"""六个样例脚本：trace 与 samples/expected/<同名>.txt 逐字节相同，且可复现。"""

import hashlib
import json
import unittest
from pathlib import Path

from snapshotdb import run_script

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "samples" / "scripts"
EXPECTED = ROOT / "samples" / "expected"


def trace_text(script):
    return "\n".join(run_script(script)) + "\n"


class SampleTraceTest(unittest.TestCase):
    def test_all_scripts_match_expected(self):
        paths = sorted(SCRIPTS.glob("*.json"), key=lambda p: p.name)
        self.assertEqual(len(paths), 6)
        for path in paths:
            with self.subTest(script=path.name):
                script = json.loads(path.read_text(encoding="utf-8"))
                expected = (EXPECTED / (path.stem + ".txt")).read_bytes()
                self.assertEqual(trace_text(script).encode("utf-8"), expected)

    def test_trace_is_deterministic(self):
        for path in sorted(SCRIPTS.glob("*.json"), key=lambda p: p.name):
            with self.subTest(script=path.name):
                script = json.loads(path.read_text(encoding="utf-8"))
                first = hashlib.sha256(trace_text(script).encode()).hexdigest()
                second = hashlib.sha256(trace_text(script).encode()).hexdigest()
                self.assertEqual(first, second)

    def test_trace_file_format(self):
        for path in sorted(SCRIPTS.glob("*.json"), key=lambda p: p.name):
            with self.subTest(script=path.name):
                script = json.loads(path.read_text(encoding="utf-8"))
                raw = trace_text(script).encode("utf-8")
                self.assertTrue(raw.endswith(b"\n"))
                self.assertNotIn(b"\r", raw)
                self.assertTrue(all(b < 128 for b in raw))
                for line in raw.decode("ascii").splitlines():
                    self.assertEqual(len(line.split("|")), 7, line)


if __name__ == "__main__":
    unittest.main()
