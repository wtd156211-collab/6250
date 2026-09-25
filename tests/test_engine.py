"""引擎语义单测：快照、读自己的写、冲突判定顺序、with= 口径、水位回收。"""

import unittest

from snapshotdb.engine import Engine, run_script


def script(events):
    return {"name": "t", "events": events}


def ev(t, op, tx, key=None, value=None):
    e = {"t": t, "op": op, "tx": tx}
    if key is not None:
        e["key"] = key
    if value is not None:
        e["value"] = value
    return e


class SnapshotTest(unittest.TestCase):
    def test_snapshot_reads_are_stable(self):
        lines = run_script(script([
            ev(0, "begin", "T0"), ev(0, "write", "T0", "k1", 1), ev(0, "commit", "T0"),
            ev(1, "begin", "R1"),
            ev(2, "begin", "W1"), ev(2, "write", "W1", "k1", 2), ev(2, "commit", "W1"),
            ev(3, "read", "R1", "k1"),
            ev(4, "read", "R1", "k1"),
            ev(5, "commit", "R1"),
        ]))
        reads = [l for l in lines if "|READ|" in l]
        self.assertEqual(reads, ["3|READ|R1|k1|1|1|-", "4|READ|R1|k1|1|1|-"])

    def test_read_own_write(self):
        lines = run_script(script([
            ev(0, "begin", "T0"),
            ev(1, "write", "T0", "k1", 7),
            ev(2, "read", "T0", "k1"),
            ev(3, "commit", "T0"),
        ]))
        self.assertIn("2|READ|T0|k1|-|7|-", lines)

    def test_read_miss_then_visible_after_snapshot(self):
        lines = run_script(script([
            ev(0, "begin", "R1"),
            ev(1, "read", "R1", "k1"),
            ev(2, "begin", "W1"), ev(2, "write", "W1", "k1", 9), ev(2, "commit", "W1"),
            ev(3, "read", "R1", "k1"),
            ev(4, "rollback", "R1"),
        ]))
        reads = [l for l in lines if "|READ|" in l]
        self.assertEqual(reads, ["1|READ|R1|k1|0|null|-", "3|READ|R1|k1|0|null|-"])


class ConflictTest(unittest.TestCase):
    def test_read_write_conflict_on_read_miss(self):
        # 读到空也进读集：别人后来装了版本就判 read_write
        lines = run_script(script([
            ev(0, "begin", "T0"),
            ev(1, "read", "T0", "k1"),
            ev(2, "begin", "T1"), ev(2, "write", "T1", "k1", 1), ev(2, "commit", "T1"),
            ev(3, "write", "T0", "k2", 2),
            ev(4, "commit", "T0"),
        ]))
        self.assertIn("4|ABORT|T0|k1|-|-|reason=read_write with=T1 versions=1", lines)

    def test_write_write_checked_before_read_write(self):
        # 同一键两类都命中时取 write_write
        lines = run_script(script([
            ev(0, "begin", "T0"), ev(0, "write", "T0", "k1", 1), ev(0, "commit", "T0"),
            ev(1, "begin", "T1"),
            ev(1, "read", "T1", "k1"),
            ev(2, "begin", "T2"), ev(2, "write", "T2", "k1", 2), ev(2, "commit", "T2"),
            ev(3, "write", "T1", "k1", 3),
            ev(4, "commit", "T1"),
        ]))
        self.assertIn("4|ABORT|T1|k1|-|-|reason=write_write with=T2 versions=1", lines)

    def test_with_is_writer_of_smallest_newer_version(self):
        lines = run_script(script([
            ev(0, "begin", "T0"), ev(0, "write", "T0", "k1", 1), ev(0, "commit", "T0"),
            ev(1, "begin", "V"),
            ev(2, "begin", "A"), ev(2, "write", "A", "k1", 2), ev(2, "commit", "A"),
            ev(3, "begin", "B"), ev(3, "write", "B", "k1", 3), ev(3, "commit", "B"),
            ev(4, "write", "V", "k1", 4),
            ev(5, "commit", "V"),
        ]))
        self.assertIn("5|ABORT|V|k1|-|-|reason=write_write with=A versions=1", lines)

    def test_first_key_in_ascending_order_decides(self):
        lines = run_script(script([
            ev(0, "begin", "T0"), ev(0, "write", "T0", "a", 1),
            ev(0, "write", "T0", "b", 1), ev(0, "commit", "T0"),
            ev(1, "begin", "V"),
            ev(2, "begin", "W"), ev(2, "write", "W", "a", 2),
            ev(2, "write", "W", "b", 2), ev(2, "commit", "W"),
            ev(3, "write", "V", "b", 3),
            ev(3, "write", "V", "a", 3),
            ev(4, "commit", "V"),
        ]))
        aborts = [l for l in lines if "|ABORT|" in l]
        self.assertEqual(aborts, ["4|ABORT|V|a|-|-|reason=write_write with=W versions=2"])

    def test_readonly_commit_never_aborts_and_takes_seq(self):
        lines = run_script(script([
            ev(0, "begin", "T0"), ev(0, "write", "T0", "k1", 1), ev(0, "commit", "T0"),
            ev(1, "begin", "R1"),
            ev(2, "begin", "W1"), ev(2, "write", "W1", "k1", 2), ev(2, "commit", "W1"),
            ev(3, "read", "R1", "k1"),
            ev(4, "commit", "R1"),
        ]))
        self.assertIn("4|COMMIT|R1|-|-|-|ver=3 wrote=- versions=1", lines)


class ReclaimTest(unittest.TestCase):
    def test_no_active_txns_keeps_one_version_per_key(self):
        eng = Engine()
        for i in range(5):
            t = "T%d" % i
            for e in (ev(i * 3, "begin", t), ev(i * 3 + 1, "write", t, "k1", i),
                      ev(i * 3 + 2, "commit", t)):
                eng.apply(e)
        self.assertEqual(eng.version_count, 1)
        chain = eng.chains["k1"]
        self.assertEqual([e[0] for e in chain.entries[chain.base:]], [5])

    def test_long_txn_pins_versions_then_releases(self):
        eng = Engine()
        eng.apply(ev(0, "begin", "L"))  # snapshot=0，只读长事务
        for i in range(1, 6):
            t = "T%d" % i
            eng.apply(ev(i, "begin", t))
            eng.apply(ev(i, "write", t, "k1", i))
            eng.apply(ev(i, "commit", t))
        self.assertEqual(eng.version_count, 5)  # 长事务拖着，一条不收
        eng.apply(ev(6, "commit", "L"))
        self.assertEqual(eng.version_count, 1)  # 长事务一结束就收回去

    def test_reclaim_keeps_baseline_and_all_above_watermark(self):
        eng = Engine()
        eng.apply(ev(0, "begin", "P"))  # snapshot=0
        for i in range(1, 5):
            t = "T%d" % i
            eng.apply(ev(i, "begin", t))
            eng.apply(ev(i, "write", t, "k1", i))
            eng.apply(ev(i, "commit", t))
        eng.apply(ev(5, "begin", "Q"))  # snapshot=4
        eng.apply(ev(6, "commit", "P"))  # 水位升到 4：留基线 ver=4，以上没有
        chain = eng.chains["k1"]
        self.assertEqual([e[0] for e in chain.entries[chain.base:]], [4])
        eng.apply(ev(7, "rollback", "Q"))

    def test_watermark_uses_min_active_snapshot(self):
        eng = Engine()
        eng.apply(ev(0, "begin", "A"))  # snapshot=0
        eng.apply(ev(0, "begin", "B"))  # snapshot=0
        eng.apply(ev(1, "begin", "W"))
        eng.apply(ev(1, "write", "W", "k1", 1))
        eng.apply(ev(1, "commit", "W"))
        self.assertEqual(eng._watermark(), 0)
        eng.apply(ev(2, "rollback", "A"))
        self.assertEqual(eng._watermark(), 0)
        eng.apply(ev(3, "rollback", "B"))
        self.assertEqual(eng._watermark(), 1)  # 无活跃事务时 w = seq


if __name__ == "__main__":
    unittest.main()
