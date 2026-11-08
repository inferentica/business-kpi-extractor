"""The task that classifies a furnished filing."""

from __future__ import annotations



CLASSIFY_TASK = """Classify this furnished filing by what it is:
- "earnings_release": the company's quarterly or annual results announcement (press release, shareholder letter, \
management report with the quarter's results).
- "financial_report": quarterly or annual financial statements with notes (e.g. a foreign issuer's interim report).
- "presentation": the results presentation or slides for a quarter.
- "other": anything else (governance, dividends, debt offerings, monthly sales, investor days, M&A).
Only the company's own consolidated results count. The results or statements of another legal entity are "other": a \
subsidiary's report, or the parent company's standalone (non-consolidated) statements (for UBS Group: UBS AG's or \
UBS Switzerland AG's reports, and "UBS Group AG standalone financial statements").

JSON shape: {"kind": "earnings_release"}"""
