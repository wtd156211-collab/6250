"""Unit tests for the MVCC engine semantics."""

import unittest

from snapshotdb.engine import Engine


def seed(eng, key, value):
    """Commit a single-write transaction; returns its version."""
    tx = "S_%s_%s" % (key, value)
    eng.begin(tx)
    eng.write(tx, key, value)
    res = eng.commit(tx)
    assert res[0] == "committed"
    return res[1]


class SnapshotTests(unittest.TestCase):
    def test_snapshot_is_stable_across_commits(self):
        eng = Engine()
        seed(eng, "k1", 10)
        eng.begin("T1")
        eng.begin("T2")
        eng.write("T2", "k1", 20)
        eng.commit("T2")
        self.assertEqual(eng.read("T1", "k1"), ("hit", 1, 10))
        self.assertEqual(eng.read("T1", "k1"), ("hit", 1, 10))

    def test_read_own_write(self):
        eng = Engine()
        seed(eng, "k1", 1)
        eng.begin("T1")
        eng.write("T1", "k1", 5)
        self.assertEqual(eng.read("T1", "k1"), ("own", 5))

    def test_read_missing_key(self):
        eng = Engine()
        eng.begin("T1")
        self.assertEqual(eng.read("T1", "nope"), ("miss",))

    def test_readonly_commit_takes_seq_but_installs_nothing(self):
        eng = Engine()
        seed(eng, "k1", 1)
        eng.begin("R1")
        eng.read("R1", "k1")
        res = eng.commit("R1")
        self.assertEqual(res, ("committed", 2))
        self.assertEqual(len(eng.chains["k1"]), 1)

    def test_rollback_discards_writes_and_seq(self):
        eng = Engine()
        seed(eng, "k1", 1)
        eng.begin("T1")
        eng.write("T1", "k1", 9)
        eng.rollback("T1")
        self.assertEqual(eng.seq, 1)
        self.assertEqual(eng.txns["T1"].state, "rolledback")
        eng.begin("T2")
        self.assertEqual(eng.read("T2", "k1"), ("hit", 1, 1))


class ConflictTests(unittest.TestCase):
    def test_write_write_conflict(self):
        eng = Engine()
        seed(eng, "k1", 10)
        eng.begin("T1")
        eng.begin("T2")
        eng.write("T1", "k1", 11)
        eng.write("T2", "k1", 22)
        self.assertEqual(eng.commit("T1"), ("committed", 2))
        self.assertEqual(eng.commit("T2"), ("aborted", "write_write", "k1", "T1"))

    def test_read_write_conflict(self):
        eng = Engine()
        seed(eng, "k1", 10)
        eng.begin("T1")
        eng.read("T1", "k1")
        eng.write("T1", "k2", 5)
        eng.begin("T2")
        eng.write("T2", "k1", 20)
        eng.commit("T2")
        self.assertEqual(eng.commit("T1"), ("aborted", "read_write", "k1", "T2"))

    def test_read_write_conflict_on_missing_key(self):
        eng = Engine()
        eng.begin("T1")
        self.assertEqual(eng.read("T1", "kx"), ("miss",))
        eng.write("T1", "ky", 1)
        eng.begin("T2")
        eng.write("T2", "kx", 7)
        eng.commit("T2")
        self.assertEqual(eng.commit("T1"), ("aborted", "read_write", "kx", "T2"))

    def test_write_write_checked_before_read_write(self):
        eng = Engine()
        seed(eng, "k1", 1)
        seed(eng, "k2", 2)
        eng.begin("T1")
        eng.read("T1", "k2")
        eng.write("T1", "k1", 9)
        eng.begin("T2")
        eng.write("T2", "k1", 8)
        eng.write("T2", "k2", 8)
        eng.commit("T2")
        res = eng.commit("T1")
        self.assertEqual(res, ("aborted", "write_write", "k1", "T2"))

    def test_with_is_writer_of_smallest_newer_version(self):
        eng = Engine()
        seed(eng, "k1", 0)
        eng.begin("T1")  # snapshot 1
        eng.write("T1", "k1", 1)
        eng.begin("T2")
        eng.write("T2", "k1", 2)
        eng.commit("T2")  # ver 2
        eng.begin("T3")
        eng.write("T3", "k1", 3)
        eng.commit("T3")  # ver 3
        self.assertEqual(eng.commit("T1"), ("aborted", "write_write", "k1", "T2"))

    def test_trigger_key_follows_sorted_order(self):
        eng = Engine()
        seed(eng, "ka", 1)
        seed(eng, "kb", 1)
        eng.begin("T1")
        eng.write("T1", "kb", 2)
        eng.write("T1", "ka", 2)
        eng.begin("T2")
        eng.write("T2", "ka", 3)
        eng.write("T2", "kb", 3)
        eng.commit("T2")
        res = eng.commit("T1")
        self.assertEqual(res[2], "ka")

    def test_readonly_commit_is_not_validated(self):
        eng = Engine()
        seed(eng, "k1", 1)
        eng.begin("R1")
        eng.read("R1", "k1")
        eng.begin("W1")
        eng.write("W1", "k1", 2)
        eng.commit("W1")
        self.assertEqual(eng.commit("R1"), ("committed", 3))


class ReclaimTests(unittest.TestCase):
    def test_watermark_tracks_oldest_active_snapshot(self):
        eng = Engine()
        seed(eng, "k1", 1)
        eng.begin("T1")
        eng.begin("T2")
        self.assertEqual(eng.watermark(), 1)
        eng.rollback("T1")
        self.assertEqual(eng.watermark(), 1)
        eng.rollback("T2")
        self.assertEqual(eng.watermark(), eng.seq)

    def test_long_txn_pins_versions_then_reclaims(self):
        eng = Engine()
        seed(eng, "k1", 0)
        eng.begin("L")  # snapshot 1
        eng.read("L", "k1")
        for i in range(1, 6):
            seed(eng, "k1", i)
            eng.reclaim()
        self.assertEqual(len(eng.chains["k1"]), 6)
        self.assertEqual(eng.retained, 6)
        eng.commit("L")
        w, dropped = eng.reclaim()
        self.assertEqual(w, eng.seq)
        self.assertEqual(len(dropped), 1)
        key, gone, kept = dropped[0]
        self.assertEqual(key, "k1")
        self.assertEqual(gone, [1, 2, 3, 4, 5])
        self.assertEqual(kept, [6])
        self.assertEqual(eng.retained, 1)

    def test_reclaim_keeps_baseline_and_everything_above(self):
        eng = Engine()
        seed(eng, "k1", 0)  # ver 1
        eng.begin("L")  # snapshot 1 -> watermark 1
        for i in range(3):
            seed(eng, "k1", i + 1)  # vers 2, 3, 4
        w, dropped = eng.reclaim()
        self.assertEqual(w, 1)
        self.assertEqual(dropped, [])
        self.assertEqual([v.ver for v in eng.chains["k1"]], [1, 2, 3, 4])

    def test_no_reclaim_lines_when_nothing_dropped(self):
        eng = Engine()
        seed(eng, "k1", 1)
        w, dropped = eng.reclaim()
        self.assertEqual(dropped, [])
        w2, dropped2 = eng.reclaim()
        self.assertEqual((w, dropped), (w2, dropped2))


if __name__ == "__main__":
    unittest.main()
