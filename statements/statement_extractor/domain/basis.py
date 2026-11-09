"""The accounting basis a statement is prepared on, and the currency it is printed in.

A foreign filer reports on its home basis (ROC GAAP for TSMC until 2012) or on IFRS, and a change of basis restates
the years before it: TSMC's FY2013 20-F prints 2012 again on IFRS. The basis is what the auditors' opinion says the
statements "present fairly … in conformity with"; a release says it in a line of its own ("All figures are based on US
GAAP"). XBRL says it by its taxonomy: us-gaap or ifrs-full concepts.
"""
from __future__ import annotations

import re

US_GAAP, IFRS, TAIWAN_IFRS, ROC_GAAP, CANADIAN_GAAP, DUTCH_GAAP = (
    "US GAAP", "IFRS", "Taiwan-IFRS", "ROC GAAP", "Canadian GAAP", "Dutch GAAP")

# Each basis by the words that name it, most specific first: IFRS "as endorsed" in Taiwan before IFRS as issued.
_NAMES = (
    (TAIWAN_IFRS, r"(?:IFRSs?|International\s+Financial\s+Reporting\s+Standards)[^.;]{0,160}?(?:endorsed|recognized)[^.;]{0,80}?"
                  r"(?:Financial\s+Supervisory\s+Commission|FSC|Republic\s+of\s+China|ROC|Taiwan)|Taiwan[-\s]IFRSs?|\bT-?IFRSs?\b|"
                  # TSMC's reviews from 2013: "the Guidelines Governing the Preparation of Financial Reports by Securities
                  # Issuers, International Financial Reporting Standard 1 … and International Accounting Standard 34"
                  r"(?:Regulations|Guidelines)\s+Governing\s+the\s+Preparation\s+of\s+Financial\s+Reports\s+by\s+Securities\s+"
                  r"Issuers[^.;]{0,300}?(?:IFRS|IAS\b|International\s+(?:Financial\s+Reporting|Accounting)\s+Standards?)"),
    (ROC_GAAP, r"(?:accounting\s+principles|GAAP)\s+generally\s+accepted\s+in\s+(?:the\s+Republic\s+of\s+China|Taiwan)|"
               r"generally\s+accepted\s+accounting\s+principles\s+in\s+Taiwan|\bTaiwan\s+GAAP\b|"
               r"generally\s+accepted\s+accounting\s+principles\s+(?:in|of)\s+the\s+Republic\s+of\s+China|\bR\.?O\.?C\.?\s+GAAP\b"),
    (US_GAAP, r"accounting\s+principles\s+generally\s+accepted\s+in\s+the\s+United\s+States|"
              r"generally\s+accepted\s+accounting\s+principles\s+(?:in|of)\s+the\s+United\s+States|\bU\.?\s?S\.?\s+GAAP\b"),
    (CANADIAN_GAAP, r"accounting\s+principles\s+generally\s+accepted\s+in\s+Canada|Canadian\s+generally\s+accepted|"
                    r"\bCanadian\s+GAAP\b"),
    (DUTCH_GAAP, r"accounting\s+principles\s+generally\s+accepted\s+in\s+the\s+Netherlands|\bDutch\s+GAAP\b"),
    # ASML's statutory interim reports: "prepared in accordance with IAS 34", IFRS's standard for interim reports.
    (IFRS, r"International\s+Financial\s+Reporting\s+Standards|\bIFRSs?\b|\bIAS\s*34\b"),
)
_ANY_NAME = re.compile("|".join(f"(?P<b{index}>{pattern})" for index, (_basis, pattern) in enumerate(_NAMES)), re.I)

# The auditors' opinion: "present fairly, in all material respects, … in conformity with …".
# A review's conclusion: "we are not aware of any material modifications … for them to be in conformity with …".
_OPINION = re.compile(r"(?:present\s+fairly|not\s+aware\s+of\s+any\s+material\s+modifications)\b.{0,600}?"
                      r"\b(?:in\s+conformity\s+with|in\s+accordance\s+with)\b(.{0,500})", re.I | re.S)
# A release or a statement's own note: "All figures are based on US GAAP", "prepared in accordance with IFRS".
_STATED = re.compile(r"\b(?:based\s+on|prepared\s+(?:in\s+accordance\s+with|in\s+conformity\s+with|under|on\s+the\s+basis\s+of)|"
                     r"in\s+(?:conformity|accordance)\s+with)\b(.{0,300})", re.I | re.S)


def _named(text: str) -> str | None:
    match = _ANY_NAME.search(text)
    if match is None:
        return None
    index = int(next(name for name, value in match.groupdict().items() if value is not None)[1:])
    return _NAMES[index][0]


def stated_basis(text: str) -> str | None:
    """The basis a document's statements are prepared on: the auditors' opinion's, else the first basis a sentence
    says the figures are prepared on or based on."""
    text = re.sub(r"<[^>]+>", " ", text or "")  # HTML read as its words
    text = re.sub(r"&(nbsp|#160|#xa0);", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)
    for match in _OPINION.finditer(text):
        basis = _named(match.group(1))
        if basis is not None:
            return basis
    for match in _STATED.finditer(text):
        basis = _named(match.group(1)[:200])
        if basis is not None:
            return basis
    return None


def default_basis(form: str) -> str | None:
    """A U.S. company's own reports are on U.S. GAAP; a foreign filer's basis must be stated."""
    return US_GAAP if form.upper().startswith(("10-K", "10-Q")) else None


def taxonomy_basis(concepts: list[str]) -> str | None:
    """An XBRL statement's basis from its concepts' taxonomy."""
    prefixes = [concept.split("_")[0].split(":")[0].lower() for concept in concepts]
    if any(prefix == "ifrs-full" for prefix in prefixes):
        return IFRS
    if any(prefix == "us-gaap" for prefix in prefixes):
        return US_GAAP
    return None


# Currencies by how a statement prints them, most specific first ("NT$" before "$").
_CURRENCIES = (
    ("TWD", r"NT\$|\bNTD\b|New\s+Taiwan\s+dollars?|New\s+Taiwan\b"),
    ("USD", r"US\$|U\.S\.\$|\bUSD\b|U\.\s?S\.\s+dollars?|United\s+States\s+dollars?|US\s+dollars?"),
    ("CAD", r"C\$|CDN\$|\bCAD\b|Canadian\s+dollars?"),
    ("HKD", r"HK\$|\bHKD\b|Hong\s+Kong\s+dollars?"),
    ("EUR", r"€|\bEUR\b|\beuros?\b"),
    ("NLG", r"\bNLG\b|\bguilders?\b|\bNfl\.?"),
    ("GBP", r"£|\bGBP\b|pounds?\s+sterling"),
    ("JPY", r"¥|\bJPY\b|\byen\b"),
    ("CNY", r"\bRMB\b|\bCNY\b|renminbi"),
    # KEPCO's 20-Fs head their columns "Won" and "U.S. dollars (Note 2)".
    ("KRW", r"₩|\bKRW\b|\bKorean\s+won\b|\bwon\b"),
    ("BRL", r"(?<![A-Z.])R\$|\bBRL\b|\bBrazilian\s+reais?\b|\breais\b"),
    ("CHF", r"\bCHF\b|\bSwiss\s+francs?\b"),
)
_ANY_CURRENCY = re.compile("|".join(f"(?P<c{index}>{pattern})" for index, (_code, pattern) in enumerate(_CURRENCIES)), re.I)


def currencies_named(text: str) -> list[str]:
    """The currencies a text names, in the order it first names them."""
    found: list[str] = []
    for match in _ANY_CURRENCY.finditer(text or ""):
        index = int(next(name for name, value in match.groupdict().items() if value is not None)[1:])
        code = _CURRENCIES[index][0]
        if code not in found:
            found.append(code)
    return found


_ONLY_CURRENCY = re.compile(r"^\s*(?:" + "|".join(pattern for _code, pattern in _CURRENCIES) + r"|\$)\s*$", re.I)


_NOTE = re.compile(r"\s*\(\s*notes?\s+\d+[a-z]?\s*\)\s*$", re.I)


def is_currency_word(text: str) -> bool:
    """A cell that only names a currency: "NT$", "US$", "EUR", "$", "U.S.$ (Note 4)"."""
    return bool(_ONLY_CURRENCY.match(_NOTE.sub("", text or "")))


# How a document states the currency of all its figures: Brookfield's "All currency amounts are in United States
# dollars unless otherwise stated", "the U.S. dollar is the company's reporting currency".
_STATED_CURRENCY = re.compile(
    r"\ball\s+(?:currency\s+|dollar\s+|monetary\s+)?(?:amounts|figures)\s+(?:are\s+)?(?:expressed\s+|presented\s+|stated\s+|shown\s+)?"
    r"in\s+(.{0,50})|\b(?:presentation|reporting)\s+currency\s+(?:is|of)\s+(?:the\s+)?(.{0,40})|"
    r"([A-Za-z.\s]{0,30}?)\s+is\s+the\s+(?:company|bank|group|corporation)\S*\s+(?:presentation|reporting)\s+currency", re.I)


def stated_currency(text: str) -> str | None:
    """The currency a document says all its figures are in, if it says so."""
    text = re.sub(r"\s+", " ", re.sub(r"&(nbsp|#160|#xa0);", " ", re.sub(r"<[^>]+>", " ", text or ""), flags=re.I))
    for match in _STATED_CURRENCY.finditer(text):
        named = currencies_named(next(group for group in match.groups() if group is not None))
        if named:
            return named[0]
    return None


def default_currency(form: str) -> str | None:
    """A form's currency when nothing says otherwise: a U.S. company's dollars, a Canadian filer's dollars (a 40-F
    reporting in U.S. dollars says so); a 20-F or a release must state its own."""
    form = form.upper()
    if form.startswith(("10-K", "10-Q")):
        return "USD"
    return "CAD" if form.startswith("40-F") else None
