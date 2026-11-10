"""Prints the parts of filing documents that match a pattern, for checking a figure against its filing by hand: each
matching table whole (its rows as the readers see them), and each matching passage. No AI, nothing stored.

    python -m kpi_extractor.ops.inspect --urls URL[,URL] --pattern REGEX

Runs on GitHub (the Inspect workflow): SEC answers requests from there with the identity the extractor uses."""
from __future__ import annotations

import argparse
import os
import re
import time
import urllib.request

from ..documents.document import parse_document


def fetch(url: str) -> bytes:
    agent = os.environ.get("SEC_USER_AGENT", "TradeTracker admin@inferentica.com")
    request = urllib.request.Request(url, headers={"User-Agent": agent, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--urls", required=True, help="Comma-separated sec.gov document URLs")
    parser.add_argument("--pattern", required=True, help="Regular expression (case-insensitive)")
    args = parser.parse_args(argv)
    pattern = re.compile(args.pattern, re.I)
    for url in [item.strip() for item in args.urls.split(",") if item.strip()]:
        if not url.startswith("https://www.sec.gov/"):
            print(f"== {url}: only sec.gov documents are read")
            continue
        document = parse_document(fetch(url), url)
        print(f"\n== {url}")
        for table in document.tables.values():
            if any(pattern.search(" ".join(row)) for row in table.rows) or pattern.search(table.context or ""):
                print(f"\n-- table {table.id}: {(table.context or '')[-160:]}")
                for index, row in enumerate(table.rows):
                    print(f"  {index:>3} | " + " | ".join(cell.strip() for cell in row))
        for block in document.blocks.values():
            for match in pattern.finditer(block.text):
                start, end = max(0, match.start() - 300), min(len(block.text), match.end() + 300)
                print(f"\n-- passage {block.id}: …{block.text[start:end]}…")
        time.sleep(0.5)  # well under SEC's 10 requests a second
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
