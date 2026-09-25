"""快照隔离内存存储引擎。

口径见 README 第 2 节：
- 版本是 (键, ver, 值, 写者)，ver 即提交号，从 1 起严格递增；
- begin 记 snapshot = seq，读只看 ver <= snapshot 的版本；
- 提交时先按 key 升序判写-写冲突，再按同样顺序判读-写冲突；
- 回收水位 w = 活跃事务 snapshot 的最小值（无活跃事务时 w = seq），
  每个键只留「ver <= w 中最大的那版（基线）」与所有 ver > w 的版本。

时间与调度全部来自脚本：没有线程、sleep、系统时钟与随机数。
"""

from bisect import bisect_right
from heapq import heappop, heappush

WRITE_WRITE = "write_write"
READ_WRITE = "read_write"


def _ver(entry):
    return entry[0]


class _Chain:
    """单键版本链：entries 按 ver 严格递增，base 是基线下标。

    base 之前的版本已被回收（延迟物理删除，由压缩兜底）；
    base 处是「ver <= 水位 的最大版本」，base 之后全部 ver > 水位。
    """

    __slots__ = ("entries", "base")

    def __init__(self):
        self.entries = []  # (ver, value, writer)
        self.base = 0


class _Txn:
    __slots__ = ("txid", "snapshot", "readset", "writeset")

    def __init__(self, txid, snapshot):
        self.txid = txid
        self.snapshot = snapshot
        self.readset = set()    # 含读到空、读到自己的写
        self.writeset = {}      # key -> value，提交前对别人不可见


class Engine:
    def __init__(self):
        self.seq = 0
        self.chains = {}            # key -> _Chain
        self.active = {}            # txid -> _Txn
        self.version_count = 0      # 保留版本总数（回收后口径）
        self.last_watermark = 0
        self.dirty = set()          # 上次有效回收后装过新版本的键
        self.counts = {"begin": 0, "commit": 0, "abort": 0,
                       "rollback": 0, "reclaim": 0}
        self._snap_heap = []        # 活跃事务 snapshot 的小根堆（惰性删除）
        self._snap_refs = {}        # snapshot -> 活跃引用数

    # ---------------------------------------------------------------- 事件

    def apply(self, event):
        """处理一个脚本事件，返回该事件产生的 trace 行（含 RECLAIM 行）。"""
        return getattr(self, "_do_" + event["op"])(event)

    def _do_begin(self, event):
        tx = _Txn(event["tx"], self.seq)
        self.active[tx.txid] = tx
        heappush(self._snap_heap, tx.snapshot)
        self._snap_refs[tx.snapshot] = self._snap_refs.get(tx.snapshot, 0) + 1
        self.counts["begin"] += 1
        return ["%d|BEGIN|%s|-|-|-|snapshot=%d" % (event["t"], tx.txid, tx.snapshot)]

    def _do_read(self, event):
        tx = self.active[event["tx"]]
        key = event["key"]
        tx.readset.add(key)
        if key in tx.writeset:
            ver_s, val_s = "-", str(tx.writeset[key])
        else:
            ver_s, val_s = "0", "null"
            chain = self.chains.get(key)
            if chain is not None:
                i = bisect_right(chain.entries, tx.snapshot,
                                 lo=chain.base, key=_ver) - 1
                if i >= chain.base:
                    ver_s = str(chain.entries[i][0])
                    val_s = str(chain.entries[i][1])
        return ["%d|READ|%s|%s|%s|%s|-" % (event["t"], tx.txid, key, ver_s, val_s)]

    def _do_write(self, event):
        tx = self.active[event["tx"]]
        tx.writeset[event["key"]] = event["value"]
        return ["%d|WRITE|%s|%s|-|%s|-" % (event["t"], tx.txid,
                                           event["key"], event["value"])]

    def _do_commit(self, event):
        t = event["t"]
        tx = self.active.pop(event["tx"])
        self._release_snapshot(tx.snapshot)
        conflict = self._check_conflict(tx)
        if conflict is not None:
            reason, key, other = conflict
            self.counts["abort"] += 1
            reclaim_lines = self._reclaim(t)
            line = ("%d|ABORT|%s|%s|-|-|reason=%s with=%s versions=%d"
                    % (t, tx.txid, key, reason, other, self.version_count))
            return [line] + reclaim_lines
        self.seq += 1
        ver = self.seq
        for key, value in tx.writeset.items():
            chain = self.chains.get(key)
            if chain is None:
                chain = self.chains[key] = _Chain()
            chain.entries.append((ver, value, tx.txid))
            self.dirty.add(key)
            self.version_count += 1
        self.counts["commit"] += 1
        reclaim_lines = self._reclaim(t)
        wrote = ",".join(sorted(tx.writeset)) if tx.writeset else "-"
        line = ("%d|COMMIT|%s|-|-|-|ver=%d wrote=%s versions=%d"
                % (t, tx.txid, ver, wrote, self.version_count))
        return [line] + reclaim_lines

    def _do_rollback(self, event):
        t = event["t"]
        tx = self.active.pop(event["tx"])
        self._release_snapshot(tx.snapshot)
        self.counts["rollback"] += 1
        reclaim_lines = self._reclaim(t)
        line = ("%d|ROLLBACK|%s|-|-|-|reason=user versions=%d"
                % (t, tx.txid, self.version_count))
        return [line] + reclaim_lines

    # ---------------------------------------------------------------- 冲突

    def _check_conflict(self, tx):
        """先按 key 升序扫写集判写-写，再按同样顺序扫读集判读-写。"""
        if not tx.writeset:
            return None
        for key in sorted(tx.writeset):
            other = self._newer_writer(key, tx.snapshot)
            if other is not None:
                return (WRITE_WRITE, key, other)
        for key in sorted(tx.readset):
            other = self._newer_writer(key, tx.snapshot)
            if other is not None:
                return (READ_WRITE, key, other)
        return None

    def _newer_writer(self, key, snapshot):
        """该键上 ver > snapshot 中最小那版的写者；没有则 None。"""
        chain = self.chains.get(key)
        if chain is None:
            return None
        i = bisect_right(chain.entries, snapshot, lo=chain.base, key=_ver)
        if i < len(chain.entries):
            return chain.entries[i][2]
        return None

    # ---------------------------------------------------------------- 回收

    def _release_snapshot(self, snapshot):
        refs = self._snap_refs[snapshot] - 1
        if refs:
            self._snap_refs[snapshot] = refs
        else:
            del self._snap_refs[snapshot]

    def _watermark(self):
        if not self.active:
            return self.seq
        heap = self._snap_heap
        while heap:
            snap = heap[0]
            if snap in self._snap_refs:
                return snap
            heappop(heap)
        return self.seq  # 不会走到：active 非空则堆中必有有效 snapshot

    def _reclaim(self, t):
        """水位较上次回收有推进时，扫 dirty 键回收；返回 RECLAIM 行。"""
        w = self._watermark()
        if w <= self.last_watermark:
            return []
        dropped_by_key = []
        for key in self.dirty:
            chain = self.chains[key]
            entries = chain.entries
            i = chain.base
            n = len(entries)
            while i + 1 < n and entries[i + 1][0] <= w:
                i += 1
            if i > chain.base:
                dropped = [e[0] for e in entries[chain.base:i]]
                kept = [e[0] for e in entries[i:]]
                self.version_count -= i - chain.base
                chain.base = i
                dropped_by_key.append((key, dropped, kept))
                if chain.base >= 64 and chain.base * 2 >= len(entries):
                    del entries[:chain.base]
                    chain.base = 0
        self.dirty.clear()
        self.last_watermark = w
        lines = []
        for key, dropped, kept in sorted(dropped_by_key):
            lines.append("%d|RECLAIM|-|%s|-|-|watermark=%d dropped=%s kept=%s"
                         % (t, key, w,
                            ",".join(str(v) for v in dropped),
                            ",".join(str(v) for v in kept)))
        self.counts["reclaim"] += len(lines)
        return lines


def run_script(script):
    """按脚本顺序跑完全部事件，返回 trace 行列表（末行是 SUMMARY）。"""
    engine = Engine()
    lines = []
    last_t = 0
    for event in script["events"]:
        last_t = event["t"]
        lines.extend(engine.apply(event))
    c = engine.counts
    lines.append("%d|SUMMARY|-|-|-|-|abort=%d begin=%d commit=%d "
                 "reclaim=%d rollback=%d versions=%d"
                 % (last_t, c["abort"], c["begin"], c["commit"],
                    c["reclaim"], c["rollback"], engine.version_count))
    return lines
