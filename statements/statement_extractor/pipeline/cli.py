"""The command line: read one company, or report on a set of reads."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from ..quality.golden import report_text, score
from ..quality.report import report
from .company import read_company
from .handcheck import write_rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="statement-extractor")
    commands = parser.add_subparsers(dest="command", required=True)
    read = commands.add_parser("read", help="Read one company's statements into files")
    read.add_argument("--symbol", required=True)
    read.add_argument("--since", default="2001-01-01")
    read.add_argument("--out", required=True)
    scored = commands.add_parser("report", help="Score a set of reads")
    scored.add_argument("folders", nargs="+")
    rows = commands.add_parser("rows", help="Raw printed rows for hand checks of sampled served lines")
    rows.add_argument("samples")
    rows.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.command == "rows":
        write_rows(Path(args.samples), Path(args.out))
        return 0
    if args.command == "read":
        summary = read_company(args.symbol.upper(), date.fromisoformat(args.since), Path(args.out))
        print(json.dumps({key: summary[key] for key in ("symbol", "periods", "by_status", "own_kept", "own_expected")}))
        return 0
    folders = [Path(folder) for folder in args.folders]
    scored = score(folders)
    text = report([Path(folder) for folder in args.folders]) + "\n\n" + report_text(scored)
    print(text)
    Path("golden_results.json").write_text(json.dumps(scored, default=str, indent=1))
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a") as handle:
            handle.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
