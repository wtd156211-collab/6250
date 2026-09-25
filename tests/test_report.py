"""Report page and CLI tests."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from snapshotdb.__main__ import main
from snapshotdb.report import collect_sections, render_html

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "samples" / "scripts"


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.sections = collect_sections(SCRIPTS)

    def test_covers_every_script_in_filename_order(self):
        names = [name for name, _ in self.sections]
        self.assertEqual(names, sorted(p.stem for p in SCRIPTS.glob("*.json")))

    def test_render_is_byte_identical_across_runs(self):
        self.assertEqual(render_html(self.sections), render_html(self.sections))

    def test_page_is_self_contained(self):
        html = render_html(self.sections)
        for bad in ("http://", "https://", "fetch(", "<script src", "<link", "<img"):
            self.assertNotIn(bad, html)

    def test_page_contains_trace_derived_facts(self):
        html = render_html(self.sections)
        for name, _ in self.sections:
            self.assertIn(name, html)
        # facts that only exist in the traces must reach the page payload
        self.assertIn("reason=read_write with=T2", html)
        self.assertIn("watermark=8 dropped=1,2,3,4,5,6 kept=7", html)
        self.assertIn("abort=2 begin=6 commit=4 reclaim=3 rollback=0 versions=1", html)


class CliTests(unittest.TestCase):
    def test_trace_command_matches_expected(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "o.txt"
            rc = main(["trace", str(SCRIPTS / "readonly_txn.json"), str(out)])
            self.assertEqual(rc, 0)
            want = (ROOT / "samples" / "expected" / "readonly_txn.txt").read_bytes()
            self.assertEqual(out.read_bytes(), want)

    def test_report_command_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            p1 = Path(tmp) / "a.html"
            p2 = Path(tmp) / "b.html"
            cwd = Path.cwd()
            try:
                os.chdir(ROOT)
                self.assertEqual(main(["report", str(p1)]), 0)
                self.assertEqual(main(["report", str(p2)]), 0)
            finally:
                os.chdir(cwd)
            self.assertEqual(p1.read_bytes(), p2.read_bytes())
            self.assertIn(b"canvas", p1.read_bytes())

    def test_module_invocation_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "o.txt"
            proc = subprocess.run(
                [sys.executable, "-m", "snapshotdb", "trace",
                 str(SCRIPTS / "same_time_order.json"), str(out)],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
