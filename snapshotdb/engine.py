"""In-memory MVCC store with snapshot isolation and write-conflict detection.

Time and scheduling are injected by the caller: events arrive in script
order and the engine never reads clocks, randomness, or thread state, so
the same script always produces the same outcome.
"""

from bisect import bisect_right
from heapq import heappop, heappush


def _ver(version):
    return version.ver


class Version:
    __slots__ = ("ver", "value", "writer")

    def __init__(self, ver, value, writer):
        self.ver = ver
        self.value = value
        self.writer = writer


class Transaction:
    __slots__ = ("txid", "snapshot", "reads", "writes", "state")

    def __init__(self, txid, snapshot):
        self.txid = txid
        self.snapshot = snapshot
        self.reads = set()
        self.writes = {}
        self.state = "active"


class Engine:
    def __init__(self):
        self.seq = 0
        self.chains = {}        # key -> list[Version], ascending ver
        self.txns = {}          # txid -> Transaction
        self.retained = 0       # total versions kept across all chains
        self._snap_heap = []    # snapshots of active txns (lazy delete)
        self._snap_live = {}    # snapshot -> live refcount
        self._multi = set()     # keys currently holding more than one version
        self._last_w = 0        # watermark used by the last reclaim pass
        self._installed = False  # versions installed since the last reclaim

    # -- transaction lifecycle ---------------------------------------
    def begin(self, txid):
        txn = Transaction(txid, self.seq)
        self.txns[txid] = txn
        heappush(self._snap_heap, txn.snapshot)
        self._snap_live[txn.snapshot] = self._snap_live.get(txn.snapshot, 0) + 1
        return txn.snapshot

    def commit(self, txid):
        txn = self.txns[txid]
        if txn.writes:
            hit = self._check_conflict(txn)
            if hit is not None:
                reason, key, other = hit
                txn.state = "aborted"
                txn.writes.clear()
                self._deactivate(txn)
                return ("aborted", reason, key, other)
        self.seq += 1
        ver = self.seq
        for key in sorted(txn.writes):
            self._install(key, ver, txn.writes[key], txid)
        txn.state = "committed"
        self._deactivate(txn)
        return ("committed", ver)

    def rollback(self, txid):
        txn = self.txns[txid]
        txn.state = "rolledback"
        txn.writes.clear()
        self._deactivate(txn)

    # -- reads and writes --------------------------------------------
    def read(self, txid, key):
        txn = self.txns[txid]
        txn.reads.add(key)
        if key in txn.writes:
            return ("own", txn.writes[key])
        chain = self.chains.get(key)
        if chain:
            i = bisect_right(chain, txn.snapshot, key=_ver) - 1
            if i >= 0:
                version = chain[i]
                return ("hit", version.ver, version.value)
        return ("miss",)

    def write(self, txid, key, value):
        self.txns[txid].writes[key] = value

    # -- conflict detection ------------------------------------------
    def _check_conflict(self, txn):
        for key in sorted(txn.writes):
            version = self._newer(key, txn.snapshot)
            if version is not None:
                return ("write_write", key, version.writer)
        for key in sorted(txn.reads):
            version = self._newer(key, txn.snapshot)
            if version is not None:
                return ("read_write", key, version.writer)
        return None

    def _newer(self, key, snapshot):
        """Smallest version with ver > snapshot on key, or None."""
        chain = self.chains.get(key)
        if not chain:
            return None
        i = bisect_right(chain, snapshot, key=_ver)
        return chain[i] if i < len(chain) else None

    # -- version store ------------------------------------------------
    def _install(self, key, ver, value, writer):
        chain = self.chains.setdefault(key, [])
        chain.append(Version(ver, value, writer))
        if len(chain) == 2:
            self._multi.add(key)
        self.retained += 1
        self._installed = True

    def _deactivate(self, txn):
        self._snap_live[txn.snapshot] -= 1

    def watermark(self):
        heap = self._snap_heap
        live = self._snap_live
        while heap and live.get(heap[0], 0) == 0:
            heappop(heap)
        return heap[0] if heap else self.seq

    def reclaim(self):
        """Drop versions below the watermark, keeping the baseline.

        Returns (watermark, [(key, dropped_vers, kept_vers), ...]) with
        keys sorted ascending; only keys that actually lost versions are
        listed.  A pass is skipped when neither the watermark moved nor
        any version was installed since the previous pass.
        """
        w = self.watermark()
        if w == self._last_w and not self._installed:
            return w, []
        self._last_w = w
        self._installed = False
        results = []
        singles = []
        for key in sorted(self._multi):
            chain = self.chains[key]
            i = bisect_right(chain, w, key=_ver)
            if i <= 1:
                continue
            dropped = [v.ver for v in chain[:i - 1]]
            del chain[:i - 1]
            self.retained -= len(dropped)
            results.append((key, dropped, [v.ver for v in chain]))
            if len(chain) == 1:
                singles.append(key)
        for key in singles:
            self._multi.discard(key)
        return w, results
