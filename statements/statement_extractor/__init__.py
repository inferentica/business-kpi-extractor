"""The three financial statements (income statement, balance sheet, cash flows) from SEC filings, by rules only.

Separate from the business KPI extractor: its own readers, checks, runs and storage. It shares only the base layer
(EDGAR access, fiscal labels, document parsing), through `base.py`, and never calls an AI.
"""
