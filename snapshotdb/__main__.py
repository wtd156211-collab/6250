"""Command line entry: python -m snapshotdb trace|report ..."""

import argparse
import sys
from pathlib import Path

from .report import collect_sections, render_html
from .runner import load_script, run_script


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="snapshotdb",
        description="Snapshot-isolated in-memory store: run timing scripts, emit traces.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_trace = sub.add_parser("trace", help="run one script and write its trace")
    p_trace.add_argument("script", help="timing script (JSON)")
    p_trace.add_argument("trace", help="output trace file")
    p_report = sub.add_parser("report", help="inline all sample scripts into one HTML page")
    p_report.add_argument("page", help="output HTML file")
    args = parser.parse_args(argv)

    if args.command == "trace":
        script = load_script(args.script)
        text = run_script(script)
        Path(args.trace).write_text(text, encoding="ascii", newline="")
        print(
            "trace: %s -> %s (%d lines)" % (args.script, args.trace, text.count("\n")),
            file=sys.stderr,
        )
    else:
        sections = collect_sections()
        html = render_html(sections)
        Path(args.page).write_text(html, encoding="utf-8", newline="")
        print(
            "report: %d scripts from samples/scripts -> %s" % (len(sections), args.page),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
