"""Script loading and trace generation."""

import json
from pathlib import Path

from .engine import Engine


def load_script(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _emit_reclaims(lines, t, watermark, dropped):
    for key, gone, kept in dropped:
        lines.append(
            "%d|RECLAIM|-|%s|-|-|watermark=%d dropped=%s kept=%s"
            % (t, key, watermark, ",".join(map(str, gone)), ",".join(map(str, kept)))
        )
    return len(dropped)


def run_script(script):
    """Run a timing script and return the trace text (ASCII lines)."""
    eng = Engine()
    lines = []
    counts = {"begin": 0, "commit": 0, "abort": 0, "rollback": 0, "reclaim": 0}
    last_t = 0
    for ev in script["events"]:
        t, op, tx = ev["t"], ev["op"], ev["tx"]
        last_t = t
        if op == "begin":
            snap = eng.begin(tx)
            counts["begin"] += 1
            line = "%d|BEGIN|%s|-|-|-|snapshot=%d" % (t, tx, snap)
        elif op == "read":
            key = ev["key"]
            res = eng.read(tx, key)
            if res[0] == "own":
                line = "%d|READ|%s|%s|-|%s|-" % (t, tx, key, res[1])
            elif res[0] == "hit":
                line = "%d|READ|%s|%s|%d|%s|-" % (t, tx, key, res[1], res[2])
            else:
                line = "%d|READ|%s|%s|0|null|-" % (t, tx, key)
        elif op == "write":
            eng.write(tx, ev["key"], ev["value"])
            line = "%d|WRITE|%s|%s|-|%s|-" % (t, tx, ev["key"], ev["value"])
        elif op == "commit":
            res = eng.commit(tx)
            if res[0] == "aborted":
                counts["abort"] += 1
                _, reason, key, other = res
                w, dropped = eng.reclaim()
                lines.append(
                    "%d|ABORT|%s|%s|-|-|reason=%s with=%s versions=%d"
                    % (t, tx, key, reason, other, eng.retained)
                )
            else:
                counts["commit"] += 1
                wrote = ",".join(sorted(eng.txns[tx].writes)) or "-"
                w, dropped = eng.reclaim()
                lines.append(
                    "%d|COMMIT|%s|-|-|-|ver=%d wrote=%s versions=%d"
                    % (t, tx, res[1], wrote, eng.retained)
                )
            counts["reclaim"] += _emit_reclaims(lines, t, w, dropped)
            continue
        elif op == "rollback":
            eng.rollback(tx)
            counts["rollback"] += 1
            w, dropped = eng.reclaim()
            lines.append("%d|ROLLBACK|%s|-|-|-|reason=user versions=%d" % (t, tx, eng.retained))
            counts["reclaim"] += _emit_reclaims(lines, t, w, dropped)
            continue
        else:
            raise ValueError("unknown op %r" % (op,))
        lines.append(line)
        w, dropped = eng.reclaim()
        counts["reclaim"] += _emit_reclaims(lines, t, w, dropped)
    lines.append(
        "%d|SUMMARY|-|-|-|-|abort=%d begin=%d commit=%d reclaim=%d rollback=%d versions=%d"
        % (
            last_t,
            counts["abort"],
            counts["begin"],
            counts["commit"],
            counts["reclaim"],
            counts["rollback"],
            eng.retained,
        )
    )
    return "\n".join(lines) + "\n"
