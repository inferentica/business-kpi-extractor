"""Wording rules: what a quote's words say about its figure (a fall, a forecast, a cause that explains a change, a
net or gross figure, a sign a bracketed list leaves unstated)."""

from __future__ import annotations

import re

from .period import _OTHER_PERIOD


# A fall named just before the number: "decreased by 16%", "a decrease of 1%", "down 5%", "the decline on a
# constant-dollar basis was 5%". Only words may stand between, so "decreased by 3% and margin was 40%" is no fall for
# the 40%, and a level ("declined to 30%", "fell from 32% to 30%") never is.
_FALL = re.compile(r"\b(?:decreas\w*|declin\w*|fell|falls?|falling|dropped|drops?|down|lower|contracted|reduc\w*)"
                   r"(?:\s+(?!to\b|from\b)[A-Za-z][\w-]*){0,6}?\s*(?:\b(?:by|of|was|were)\s*)?$", re.I)


# A statement about the future: "We expect revenue from AI to be $12 billion" (Broadcom's guidance, once read as its
# quarter). Not "above our forecast" after a figure, nor an RPO "expected to be recognized over the next 12 months".
_FORWARD = re.compile(r"\b(?:we|management|the company)\s+(?:now\s+)?(?:expects?|anticipates?|forecasts?|projects?|plans?|targets?|guides?)\b"
                      r"|\bguidance\b|\boutlook\b|\bwill be\b|\b(?:is|are) expected to\b(?! be recogni)", re.I)


# Words of a KPI's name that say nothing on their own.
_PLAIN_WORDS = {"other", "total", "revenue", "revenues", "growth", "net", "inflows", "outflows", "flows", "income", "fees", "fee", "year",
                "over", "and", "the", "of", "by", "constant", "dollar", "dollars", "excluding", "quarter", "quarterly", "sales", "volume"}


def forward_looking(text: str) -> bool:
    return bool(_FORWARD.search(text))


def _figure_forms(value: float) -> set[str]:
    """How a figure may be written: 12000000000 as "12", "12.0" or "12,000", whatever the scale word."""
    forms = set()
    for scale in (1, 1e3, 1e6, 1e9, 1e12):
        scaled = abs(value) / scale
        for digits in (0, 1, 2):
            if scaled >= 1 and abs(round(scaled, digits) - scaled) < 1e-9:
                forms.add(f"{scaled:,.{digits}f}")
    return forms


_CAUSE = re.compile(r"driven|due to|because|as a result|result(?:ed|ing)? (?:of|from)|reflect|includ|exclud|acqui|divest|"
                    r"dispos|sale of|sold|reorgani|realign|recast|restat|split|reclass|refranchis|transfer|launch|ramp|impact|"
                    r"primarily|led by|attributable|following|completion|closing|consolidat|merger|spin|separation|reduction|"
                    r"workforce|hired|change in|methodolog|definition|recovery|weather|pandemic|coronavirus|covid|strike|"
                    r"inflow|outflow|issuance|redemption|offset|beginning|now report|higher level|lower level|record", re.I)


_COMPARED = re.compile(r"compared (?:to|with)|\bversus\b|\bvs\.?\s|\bfrom \$?\d", re.I)


_NUMBER = re.compile(r"(?<![\w.])\(?\$?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\)?")


def previous_from_jump(note: str, value: float) -> float | None:
    """The previous quarter's figure a jump note was measured against ("changed 1.9x in a quarter", "moved +3.0
    points in a quarter")."""
    if match := re.match(r"changed ([\d.]+)x", note):
        ratio = float(match.group(1))
        return value / ratio if ratio else None
    if match := re.match(r"moved ([+-][\d.]+)", note):
        return value - float(match.group(1))
    return None


def _compares_to_a_small_change(quote: str, value: float, previous: float | None) -> bool:
    """A quote whose own comparison ("AUM was $233.8 billion ... compared to $221.8 billion") shows a small change
    where the jump says a large one: it restates the figure, and contradicts the jump."""
    if previous is None or not value or not previous or 0.67 < value / previous < 1.5 or not _COMPARED.search(quote):
        return False
    for form in _figure_forms(value):
        at = re.search(r"(?<![\d,.])" + re.escape(form) + r"(?![\d,])", quote)
        number = float(form.replace(",", "")) if at else 0
        if not number:
            continue
        rest = quote[:at.start()] + " " + quote[at.end():]
        others = [float(n.replace(",", "")) * value / number for n in _NUMBER.findall(rest)]
        if any(other and 0.8 < value / other < 1.25 and not 0.8 < previous / other < 1.25 for other in others):
            return True
    return False


def explanation_supports(quote: str, kpi_label: str, value: float, previous: float | None = None, strict: bool = True) -> bool:
    """Whether a quote can explain a sharp change in this figure: it states the figure or names the KPI, gives a cause
    (an acquisition, a reorganisation, a definition change, what drove it: not the figure restated), does not speak of
    the future, is not about another period, and does not itself show a small change. Broadcom's "We expect revenue
    from AI to be $12 billion for fiscal year 2024" once cleared a quarter; Apollo's "Pension Group Annuities: ..."
    cleared its Other Spread Products; planted false jumps passed on KKR's "AUM was $233.8 billion ... compared to
    $221.8 billion". strict=False is reader 5's rule, which decides re-reads of stored figures: unchanged, so this
    rule re-reads nothing."""
    if len(quote) < 20 or forward_looking(quote) or _OTHER_PERIOD.search(quote):
        return False
    if strict and (not _CAUSE.search(quote) or _compares_to_a_small_change(quote, value, previous)):
        return False
    if any(re.search(r"(?<![\d,.])" + re.escape(form) + r"(?![\d,])", quote) for form in _figure_forms(value)):
        return True
    words = {word for word in re.findall(r"[a-z][a-z0-9-]+", kpi_label.lower()) if word not in _PLAIN_WORDS and len(word) > 2}
    return any(re.search(rf"\b{re.escape(word)}\b", quote, re.I) for word in words)


def falls(quote: str, value_text: str) -> bool:
    """Whether the quote calls the figure a fall, so a change it states as a plain number is negative."""
    at = quote.find(value_text)
    return at >= 0 and bool(_FALL.search(quote[:at][-80:]))


SIGN_NOT_STATED = "the sign of a figure listed after a fall is not stated"


# A period's figure in a bracketed list: "(Q3: 73,000, Q4: 22,000)", "(Q1, -36,000, Q2: -1,000)".
_PERIOD_LIST = re.compile(r"\b(?:Q[1-4]|H[12]|FY\s?\d{0,4})\b[:,]?\s*[-\u2212]?[\d,.]+[^()]*$|\b(?:Q[1-4]|H[12])\b[:,]?\s*$", re.I)


_FALL_WORD = re.compile(r"\b(?:decreas\w*|declin\w*|fell|falls?|dropped|drops?|down|lower|contracted|reduc\w*|lost|loss(?:es)?)\b", re.I)


def listed_after_fall(quote: str, value_text: str) -> bool:
    """Whether the figure is an unsigned entry of a bracketed period list that follows a fall."""
    if value_text.lstrip().startswith(("-", "\u2212")):
        return False
    at = quote.find(value_text)
    opening = quote.rfind("(", 0, at) if at >= 0 else -1
    if opening < 0 or ")" in quote[opening:at] or quote[max(0, at - 1):at] in ("-", "\u2212"):
        return False
    return bool(_PERIOD_LIST.search(quote[opening + 1:at])) and bool(_FALL_WORD.search(quote[:opening][-120:]))


def net_gross_mismatch(text: str, kpi_label: str) -> str | None:
    """"net" when the text names a net figure and the KPI a gross one (KKR's Net Unrealized Carried Interest is not its
    Gross Unrealized Carried Interest), "gross" the other way round, else None."""
    named = {word for word in ("net", "gross") if re.search(rf"\b{word}\b", text, re.I)}
    wanted = {word for word in ("net", "gross") if re.search(rf"\b{word}\b", kpi_label, re.I)}
    if len(wanted) == 1 and len(named) == 1 and named != wanted:
        return named.pop()
    return None
