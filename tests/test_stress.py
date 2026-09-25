"""Deterministic stress test: scale, memory, and reclaim invariants."""

import time
import tracemalloc
import unittest

from snapshotdb.runner import run_script


def make_script(n_txns=8000, n_keys=40):
    """Build a deterministic script: one long reader plus many writers."""
    events = []
    t = 0
    events.append({"t": t, "op": "begin", "tx": "L"})
    for i in range(n_txns):
        tx = "W%d" % i
        t += 1
        events.append({"t": t, "op": "begin", "tx": tx})
        t += 1
        events.append({"t": t, "op": "read", "tx": tx, "key": "k%d" % (i % n_keys)})
        t += 1
        events.append({"t": t, "op": "write", "tx": tx,
                       "key": "k%d" % ((i * 13) % n_keys), "value": i})
        t += 1
        events.append({"t": t, "op": "commit", "tx": tx})
        if i % 1000 == 0:
            t += 1
            events.append({"t": t, "op": "read", "tx": "L", "key": "k0"})
    t += 1
    events.append({"t": t, "op": "commit", "tx": "L"})
    return {"name": "stress", "events": events}


class StressTests(unittest.TestCase):
    def test_scale_invariants_and_budget(self):
        script = make_script()
        self.assertLessEqual(len(script["events"]), 5 * 10**4)

        tracemalloc.start()
        start = time.monotonic()
        trace = run_script(script)
        elapsed = time.monotonic() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        self.assertLess(elapsed, 5.0, "trace took %.2fs" % elapsed)
        self.assertLess(peak, 64 * 1024 * 1024, "peak %d bytes" % peak)

        lines = [ln for ln in trace.split("\n") if ln]
        summary = lines[-1]
        self.assertIn("|SUMMARY|", summary)

        committed_keys = set()
        watermarks = []
        for ln in lines:
            f = ln.split("|")
            if f[1] == "COMMIT" and "wrote=" in f[6]:
                wrote = f[6].split("wrote=")[1].split(" ")[0]
                if wrote != "-":
                    committed_keys.update(wrote.split(","))
            elif f[1] == "RECLAIM":
                w = int(f[6].split("watermark=")[1].split(" ")[0])
                watermarks.append(w)

        # watermark never goes backwards
        self.assertEqual(watermarks, sorted(watermarks))
        # no active transactions at the end: retained == keys holding versions
        final_versions = int(summary.split("versions=")[1])
        self.assertEqual(final_versions, len(committed_keys))

    def test_stress_trace_is_reproducible(self):
        script = make_script(n_txns=200)
        self.assertEqual(run_script(script), run_script(script))


if __name__ == "__main__":
    unittest.main()
