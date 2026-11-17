"""A line's sign convention, as its name states it: of two opposite words, the one outside the parentheses is positive,
the one inside negative ("Net Recoveries (Charge-offs)": recoveries positive). A row stating the other convention
prints the opposite sign of the same figure: Northern Trust's "Net Charge-offs / (Recoveries)" (2018-20) and "Net
(Recoveries) / Charge-offs" (2017-18) name both words the other way round; for a ratio, "Net Charge-offs (Annualized)"
(2016-17), naming charge-offs alone, does too. An amount's row naming one side alone says nothing: JPMorgan's
"Investment securities losses" prints its losses in parentheses, as the income statement does."""
from __future__ import annotations

import re

# Words that name a side of a signed figure: only pairs of these state a convention ("(AUM)" or "(Annualized)" do not).
_SIGNED = {"gain", "loss", "recovery", "chargeoff", "income", "expense", "benefit", "provision", "release", "inflow",
           "outflow", "increase", "decrease", "profit", "credit", "debit", "surplus", "deficit", "appreciation",
           "depreciation", "earning", "writeoff", "writedown"}
_WORD = r"[A-Za-z][A-Za-z-]*"
_BEFORE = re.compile(rf"({_WORD})\s*/?\s*\(\s*({_WORD})\s*\)")  # "Recoveries (Charge-offs)", "Charge-offs / (Recoveries)"
_AFTER = re.compile(rf"\(\s*({_WORD})\s*\)\s*/\s*({_WORD})")  # "(Recoveries) / Charge-offs": charge-offs positive


def _word(text: str) -> str:
    word = re.sub(r"[^a-z]", "", text.lower())
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("sses"):
        return word[:-2]
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


def _pairs(label: str) -> set[tuple[str, str]]:
    """(positive, negative) pairs of signed words the label states."""
    found = {(_word(a), _word(b)) for a, b in _BEFORE.findall(label or "")}
    found |= {(_word(b), _word(a)) for a, b in _AFTER.findall(label or "")}
    return {(positive, negative) for positive, negative in found
            if positive in _SIGNED and negative in _SIGNED and positive != negative}


def _words(label: str) -> set[str]:
    return {_word(word) for word in re.findall(_WORD, label or "")}


def _outside_words(label: str) -> set[str]:
    return _words(re.sub(r"\([^)]*\)", " ", label or ""))


def opposite_convention(row_label: str, kpi_label: str, ratio: bool = False) -> bool:
    """Whether the row states the line's sign convention the other way round."""
    row_pairs = _pairs(row_label)
    for positive, negative in _pairs(kpi_label):
        if (negative, positive) in row_pairs:
            return True
        if ratio and not row_pairs and negative in _outside_words(row_label) and positive not in _words(row_label):
            return True
    return False
