"""A row of a statement's table that only states its scale or audit status, its title's caption run into the table
(readers/printed/read.py): no line, and never a heading the rows below it read under."""
from __future__ import annotations

import re

from .line_kind import SHARE_HEADING

# A row that only states the scale or the audit status: "Notes (in thousands, except per share data) EUR EUR".
# Woori's "(Korean Won in millions, except per share data)": the currency named before its scale.
_CAPTION = re.compile(r"\(\s*(?:[A-Za-z]+,?\s+){0,2}(?:in|amounts\s+in|dollars\s+in|expressed\s+in)\s+(?:[A-Z]{2,3}\s+)?(thousands|millions|billions)\b|"
                      r"^\(\s*\$?\s*000(?:,000)?(?:'?s)?(?:\s+omitted)?\s*(?:[,)]|except\b)|\(\s*\$\s*in\s+(thousands|millions|billions)\b|"
                      r"^\(?unaudited\)?$|^amounts\s+expressed\s+in\s+(thousands|millions|billions)\b|"  # Coca-Cola FEMSA's 20-Fs
                      # A caption's second line: TSMC's "Except for Per Share Amounts and Shares Outstanding)".
                      r"^\(?\s*except\s+(for\s+)?(per\s+share|share|earnings\s+per)|"
                      # A caption's wrapped tail: Ross's "($000, except per share data)"; Woori's 2010 "per common share data)
                      # per common share data)"; Oshkosh's "(In thousands, except" / "per share amounts)", which as a heading
                      # made Net sales a figure a share (round 125's review).
                      r"^(?:(?:and\s+|except\s+(?:for\s+)?)?(?:shares?\s+and\s+)?(?:income\s+)?per[-\s](?:common\s+)?share\s+"
                      r"(?:data|amounts?|figures|information)\)\s*)+$|"
                      # Embraer's 2003 releases: "in thousands of U.S. dollars except per share data", no bracket.
                      r"^in\s+(?:thousands|millions|billions)\s+of\b[^()]*\bexcept\s+(?:for\s+)?per\s+share\b|"
                      # ASML's 2020 6-Ks: "(unaudited, in millions EUR, except per share data)", any scale caption so worded.
                      r"^\(?[^()]*\b(?:thousands|millions|billions)\b[^()]*\bexcept\b[^()]*\bper[-\s]share\b", re.I)


def caption_only(label: str) -> bool:
    """A row that is a scale caption ("(In millions, except share amounts)", ASML's "For the year ended December 31
    (Amounts in thousands, except per share data)"). One that names share counts before their scale is a heading:
    Apple's "Shares used in computing earnings (loss) per share (in thousands):", whose "Basic" and "Diluted" below
    are share counts in thousands, not figures a share."""
    match = _CAPTION.search(label)
    return bool(match) and not SHARE_HEADING.search(label[:match.start()])
