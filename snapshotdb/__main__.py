"""命令行入口：

    python -m snapshotdb trace  <脚本文件> <轨迹文件>
    python -m snapshotdb report <页面文件>

诊断信息走 stderr；产物文件全是 UTF-8 / 纯 ASCII / 单 \\n / 末行有换行。
"""

import argparse
import json
import sys
from pathlib import Path

from .engine import run_script
from .report import render_page

DEFAULT_SCRIPTS_DIR = "samples/scripts"


def _write_text(path, text):
    p = Path(path)
    if p.parent != p:
        p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _cmd_trace(args):
    script_path = Path(args.script)
    script = json.loads(script_path.read_text(encoding="utf-8"))
    lines = run_script(script)
    _write_text(args.trace, "\n".join(lines) + "\n")
    print("trace: %s -> %s (%d 行)" % (script_path.name, args.trace, len(lines)),
          file=sys.stderr)
    return 0


def _cmd_report(args):
    scripts_dir = Path(args.scripts)
    paths = sorted(scripts_dir.glob("*.json"), key=lambda p: p.name)
    if not paths:
        print("report: %s 下没有脚本" % scripts_dir, file=sys.stderr)
        return 1
    sections = []
    for path in paths:
        script = json.loads(path.read_text(encoding="utf-8"))
        sections.append({"name": script["name"], "trace": run_script(script)})
        print("report: 跑完 %s" % path.name, file=sys.stderr)
    _write_text(args.page, render_page(sections))
    print("report: %d 个脚本 -> %s" % (len(sections), args.page), file=sys.stderr)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="snapshotdb", description="快照隔离内存存储引擎")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_trace = sub.add_parser("trace", help="跑一个时序脚本，写逐行轨迹")
    p_trace.add_argument("script")
    p_trace.add_argument("trace")
    p_report = sub.add_parser("report", help="全部样例内联进一个 HTML 页面")
    p_report.add_argument("page")
    p_report.add_argument("--scripts", default=DEFAULT_SCRIPTS_DIR,
                          help="脚本目录（默认 %(default)s）")
    args = parser.parse_args(argv)
    if args.cmd == "trace":
        return _cmd_trace(args)
    return _cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
