# Statement extractor

Reads the three financial statements (income statement, balance sheet, cash flows) from SEC filings, back to a
company's first filing. It works by rules only and never calls an AI.

It is separate from the business KPI extractor in this repository, with its own package, tests and workflow
(`.github/workflows/statements.yml`). It reaches the KPI extractor's base layer (EDGAR access, fiscal labels) only
through `statement_extractor/base.py`, and `tests/test_isolation.py` fails if anything else does. It edits no KPI
extractor file.

## How a period is read

1. **Every column.** Each 10-Q, 10-K, 20-F and 40-F is read from its XBRL presentation as filed. That covers its own
   period and every earlier period it presents again (a 10-K's two prior years, a 10-Q's year-ago quarter and prior
   year-end balance sheet). Labels are the filer's own, in the role its presentation asks for.
2. **Its own arithmetic.** A statement is kept only when its arithmetic holds:
   - assets equal liabilities and equity;
   - opening cash plus the change (with or without the exchange-rate effect) equals closing cash;
   - every subtotal the filer declared equals its lines, within the rounding of the reported figures.

   A statement with nothing to check is "unproven" and isn't kept.
3. **Newest filing wins.** Each period takes the newest filing whose arithmetic holds for it. The source filing is
   recorded, along with how it differs from the period's own filing (restated lines). A fourth quarter whose year was
   restated but whose nine months weren't, or the other way round, is listed: subtracting them would mix two
   presentations.

## Running it

A pilot runs on GitHub-hosted runners on every push to a `statements/**` branch. It reads the companies in
`pilot.json`, one job each, and hands back the files as artifacts:

- `readings.jsonl`: every column read, with its checks;
- `chosen.json`: one statement per period, with lines and source;
- `filings.json` and `summary.json`.

The report job scores them: coverage, kept, rejected and unproven periods, restatements, AI calls (0) and cost (¥0).
It stores nothing anywhere else.

```bash
pip install . ./statements
python -m statement_extractor read --symbol AAPL --since 2009-01-01 --out out/AAPL
python -m statement_extractor report out/*
```
