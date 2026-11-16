"""How a printed line's figure reads: an amount, an amount a share, or a count of shares, from its label and the
heading it sits under (readers/printed/read.py). Each rule is from a filing it misread (round 125's review: Oshkosh's
net sales a figure a share, Century Aluminum's share counts a figure a share, GDS's preferred dividends a count)."""
from __future__ import annotations

import re

from ...domain.printed_labels import PER_SHARE, SHARE_COUNT

# Braskem's "Preferred shares class \"B\"" and Copel's "Ordinary shares 30.1.3" (a note number) are classes too.
_SHARE_CLASS = re.compile(r"^(ordinary|common|preferred|class\s+\S+)\b.*\bshares?\b(\s+class\s+\S+)?(\s+\d+(\.\d+)*)?$", re.I)
SHARE_HEADING = re.compile(r"\bshares\b|weighted[-\s]average|\baverage\s+(number|common)", re.I)
# Words that make a heading or a row a share count, whatever per-share wording it also prints: Delta's "Weighted
# Average Shares Used In Per Share" / "Computation:", Apple's "Common and common equivalent shares used in the
# calculations of earnings (loss) per share (in thousands)", Alcon's "Basic weighted average common".
COUNTS = re.compile(r"\bweight(ed)?[-\s]+average\b|\baverage\s+(number|common|shares?)\b|\bnumber\s+of\s+(common\s+|ordinary\s+)?shares\b|"
                     r"\bshares?\s+(used|outstanding|in\s+issue)\b|\bthousands\s+of\s+shares\b", re.I)
# Money lines a per-share heading never reaches, printed after its block: Welltower's "Total comprehensive income
# (loss) attributable to stockholders", Sabesp's "Depreciation and amortization" and "EBITDA" (round 125's review).
_MONEY_AFTER = re.compile(r"^(total\s+)?(other\s+)?(comprehensive|retained|depreciation|amortization|ebitda|ebit\b|revenues?\b|"
                          r"net\s+(sales|revenues?)\b|sales\b|cost|gross\s+(profit|margin|income)|operating\s+(income|expenses?|profit|costs?)|"
                          r"selling\b|interest\s+(income|expense)|provision\s+for|income\s+tax)", re.I)
# A rate quoted in a money line's label: Oshkosh's "Class B common ( .500 per share each year)" among its dividends.
_RATE = re.compile(r"\(\s*\$?\s*\d*\.?\d+\s+(?:per|a)\s+(?:common\s+)?share\b[^)]*\)", re.I)
_BARE_BASIS = re.compile(r"\W*(basic|diluted)\W*", re.I)
# A share class's dividend a share under a dividends heading: Oshkosh's "Cash dividends:" / "Class A Common Stock
# $ 0.05000" (a class quoting its rate, "Class A common ( .435 per share each year)", is the money paid).
_DIVIDENDS = re.compile(r"^(cash\s+)?dividends(\s+(declared|paid))?(\s+per\s+(common\s+)?share)?\s*:?$", re.I)
_CLASS_ROW = re.compile(r"^(class\s+\S+\s+)?(common|preferred|ordinary)(\s+(stock|shares?))?$", re.I)




def continued_count(label: str, heading: str, counted_before: bool) -> str:
    """A bare "Basic" or "Diluted" heading after share counts goes on counting: Embraer's "Basic" / Common, Preferred,
    then "Diluted" / the same."""
    if counted_before and _BARE_BASIS.fullmatch(label) and (COUNTS.search(heading) or SHARE_HEADING.search(heading)):
        return f"{heading} {label}"
    return label


def line_kind(kind: str, label: str, wrapped: str, heading: str, has_figures: bool,
              counted_before: bool) -> tuple[bool, bool, bool, str]:
    """(per share, a share count, counted under a share-count heading, the heading that goes on). `wrapped` is the
    label with the headings above it that it ends (Apple's count label over five rows)."""
    if has_figures and _MONEY_AFTER.match(label) and PER_SHARE.search(heading) and not COUNTS.search(heading):
        heading = ""  # a per-share heading's reach ends at a money line
    own = _RATE.sub("", label)
    per_share = kind != "balance" and bool(PER_SHARE.search(own) or kind == "income" and has_figures and (PER_SHARE.search(
        heading) and (not SHARE_HEADING.search(label) or _SHARE_CLASS.match(label)) or _DIVIDENDS.match(heading)
        and _CLASS_ROW.match(label)))
    # Not a line that says what it is a share of: RPM's "BASIC AND DILUTED EARNINGS PER SHARE OF COMMON STOCK".
    under_shares = kind == "income" and (bool(SHARE_HEADING.search(heading) or COUNTS.search(heading)) and re.match(
        r"^(basic|diluted|primary|fully\s+diluted|assuming\s+dilution)\b", label, re.I) is not None and not re.search(
        r"per\s+(common\s+)?share|earnings|income|loss", label, re.I) or bool(COUNTS.search(heading)) and re.match(
        r"^(common|preferred|ordinary|class)\b", label, re.I) is not None or counted_before and _BARE_BASIS.fullmatch(label) is not None)
    # Count words name a count only before any "per": Apple's "…shares used in the calculations of earnings (loss) per
    # share" is a count, Petrobras's "Basic and diluted earnings per weighted-average of common and preferred share"
    # and Ultrapar's "EARNINGS PER SHARE (BASED ON ANNUAL WEIGHTED AVERAGE OF SHARES…)" are figures a share.
    words, per = COUNTS.search(wrapped), re.search(r"\bper\b", wrapped, re.I)
    counted = re.match(r"^(shares|weighted|average|number\s+of\s+shares)\b", label, re.I) is not None or bool(
        words and (per is None or words.start() < per.start()))
    # A share class under a per-share heading is that class's amount per share (Copel's "Class \"B\" Preferred shares").
    if not (per_share and _SHARE_CLASS.match(label) and not under_shares) and (
            under_shares or counted or re.search(r"\bshares\b", label, re.I) and not re.search(
            r"per\s+(common\s+)?share\b|earnings?\b|income|loss|per\s+(one\s+)?thousand", label, re.I)):
        per_share = False  # TSMC's "BASIC WEIGHTED AVERAGE SHARES OUTSTANDING (THOUSANDS)"
    shares = not per_share and kind == "income" and (under_shares or counted or bool(SHARE_COUNT.search(label)))
    return per_share, shares, under_shares, heading
