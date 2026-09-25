"""CLI 与页面：trace 产物逐字节对账、report 可复现、页面必现信息来自轨迹。"""

import json
import re
import tempfile
import unittest
from pathlib import Path

from snapshotdb.__main__ import main

ROOT = Path(__file__).resolve().parent.parent


class TraceCliTest(unittest.TestCase):
    def test_trace_writes_expected_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o.txt"
            for script in sorted((ROOT / "samples" / "scripts").glob("*.json")):
                with self.subTest(script=script.name):
                    rc = main(["trace", str(script), str(out)])
                    self.assertEqual(rc, 0)
                    expected = (ROOT / "samples" / "expected" / (script.stem + ".txt"))
                    self.assertEqual(out.read_bytes(), expected.read_bytes())

    def test_trace_twice_same_sha256(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            script = ROOT / "samples" / "scripts" / "same_time_order.json"
            digests = []
            for i in range(2):
                out = Path(d) / ("o%d.txt" % i)
                main(["trace", str(script), str(out)])
                digests.append(hashlib.sha256(out.read_bytes()).hexdigest())
            self.assertEqual(digests[0], digests[1])


class ReportCliTest(unittest.TestCase):
    def _build(self, path):
        rc = main(["report", str(path), "--scripts",
                   str(ROOT / "samples" / "scripts")])
        self.assertEqual(rc, 0)
        return Path(path).read_text(encoding="utf-8")

    def test_report_is_byte_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            a = self._build(Path(d) / "a.html")
            b = self._build(Path(d) / "b.html")
            self.assertEqual(a, b)

    def test_page_is_self_contained(self):
        with tempfile.TemporaryDirectory() as d:
            html = self._build(Path(d) / "p.html")
            self.assertNotIn("fetch(", html)
            self.assertNotIn("http://", html)
            self.assertNotIn("https://", html)
            self.assertNotIn("<link", html)
            self.assertNotIn("src=", html)

    def test_page_contains_trace_facts(self):
        with tempfile.TemporaryDirectory() as d:
            html = self._build(Path(d) / "p.html")
            for name in ("long_txn_reclaim", "read_write_conflict", "readonly_txn",
                         "rollback_retry", "same_time_order", "write_write_conflict"):
                self.assertIn(name, html)
            # 判定与水位数字直接嵌自轨迹
            self.assertIn("reason=write_write with=T1", html)
            self.assertIn("reason=read_write with=T2", html)
            self.assertIn("watermark=8 dropped=1,2,3,4,5,6 kept=7", html)
            self.assertIn("abort=2 begin=6 commit=4 reclaim=3 rollback=0 versions=1", html)

    def test_page_renderer_logic(self):
        # 页面 JS 里的关键渲染逻辑：刻度、行、连线数据都来自轨迹解析
        with tempfile.TemporaryDirectory() as d:
            html = self._build(Path(d) / "p.html")
            data = re.search(
                r'<script id="trace-data" type="application/json">(.*?)</script>',
                html, re.S).group(1)
            sections = json.loads(data)
            self.assertEqual([s["name"] for s in sections],
                             ["long_txn_reclaim", "read_write_conflict",
                              "readonly_txn", "rollback_retry",
                              "same_time_order", "write_write_conflict"])
            for s in sections:
                self.assertTrue(s["trace"][-1].split("|")[1] == "SUMMARY")


if __name__ == "__main__":
    unittest.main()
