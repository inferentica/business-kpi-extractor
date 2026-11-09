"""Classify rules: what a furnished filing (a foreign issuer's 6-K, a U.S. filer's 8-K outside Item 2.02) is, from its
exhibits' titles and opening lines, before any AI. A rule answers only when its wording is plain and nothing else in the
titles contradicts it: a monthly revenue report or an AGM notice is no earnings document, a quarter's results release
or management report is. Anything else (or both kinds of wording at once) is left to the AI's Classify, as before.
Scored against past classifications in evals/classify.json (kpi_extractor.evals.classify_eval)."""
from __future__ import annotations

import re

_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
_QUARTER = r"(?:first|second|third|fourth|1st|2nd|3rd|4th)[\s-]+quarter|q[1-4]|[1-4]q(?:\s*'?\d{2})?|half[\s-]year|interim|full[\s-]year|annual"

EARNINGS = [re.compile(pattern, re.I) for pattern in (
    rf"\b(?:{_QUARTER})\b[^.]{{0,60}}\b(?:results|earnings|eps|net (?:income|profit)|financial statements|management report)\b",
    r"\bquarterly (?:results|management report|report)\b",
    r"\bmanagement report\b",
    r"\binterim (?:financial )?(?:report|statements)\b",
    r"\bconsolidated financial statements\b",
    r"\breports? (?:record |strong )?(?:\w+ )?(?:quarter|q[1-4]) (?:results|eps|earnings|net)\b",
)]
OTHER = [re.compile(pattern, re.I) for pattern in (
    rf"\b(?:{_MONTHS})(?:\s+\d{{4}})?\s+(?:net\s+)?(?:revenue|sales)s?\b",
    r"\bmonthly (?:revenue|sales)\b",
    r"\b(?:annual|extraordinary|special) general (?:meeting|shareholders'? meeting)\b",
    r"\bboard (?:of directors )?(?:meeting|resolutions|approv)",
    r"\b(?:share|stock) (?:buy-?back|repurchase)s?\b",
    r"\btransactions? (?:under|in) (?:its|the) (?:share|stock)",
    r"\bnotice of\b",
    r"\b(?:cash )?dividend (?:declaration|payment|record date)\b",
)]

# What a rule reads: each exhibit's name, description and opening; the whole body would carry a results release's own
# mention of dividends or buybacks.
_TITLE_CHARS = 240


def titles(excerpt: str) -> str:
    """The classification excerpt's exhibit headers and the opening of each, as one text."""
    parts = re.split(r"\n\n", excerpt)
    return " | ".join(part[:_TITLE_CHARS] for part in parts)


def classify_by_rules(excerpt: str) -> str | None:
    """'earnings' or 'other' when the titles say so plainly, None when they say both or neither."""
    text = titles(excerpt)
    earnings = any(pattern.search(text) for pattern in EARNINGS)
    other = any(pattern.search(text) for pattern in OTHER)
    if earnings == other:
        return None
    return "earnings" if earnings else "other"
