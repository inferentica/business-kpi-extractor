"""The accounting basis a statement is prepared on, and the currency it is printed in.

A foreign filer reports on its home basis (ROC GAAP for TSMC until 2012) or on IFRS, and a change of basis restates
the years before it: TSMC's FY2013 20-F prints 2012 again on IFRS. The basis is what the auditors' opinion says the
statements "present fairly … in conformity with"; a release says it in a line of its own ("All figures are based on US
GAAP"). XBRL says it by its taxonomy: us-gaap or ifrs-full concepts.
"""
from __future__ import annotations

import html
import re

US_GAAP, IFRS, TAIWAN_IFRS, ROC_GAAP, CANADIAN_GAAP, DUTCH_GAAP = (
    "US GAAP", "IFRS", "Taiwan-IFRS", "ROC GAAP", "Canadian GAAP", "Dutch GAAP")
# National adoptions of IFRS, labelled apart from IFRS as issued by the IASB where the filing names them (decision of
# 2026-11-13): Korea's K-IFRS, Brazil's CPC pronouncements. One family with IFRS (ifrs_family).
K_IFRS, CPC_IFRS = "K-IFRS", "IFRS (CPC)"
_IASB = re.compile(r"\bIASB\b|International\s+Accounting\s+Standards\s+Board", re.I)


def ifrs_family(basis: str | None) -> str | None:
    """IFRS for IFRS as issued and its national adoptions read alike (K-IFRS, CPC); Taiwan-IFRS stays apart (its
    statements differ from TSMC's IFRS 20-F by right)."""
    return IFRS if basis in (IFRS, K_IFRS, CPC_IFRS) else basis
MEXICAN_FRS, MEXICAN_GAAP, CHILEAN_GAAP, BRAZILIAN_GAAP, KOREAN_GAAP, UK_GAAP = (
    "Mexican FRS", "Mexican GAAP", "Chilean GAAP", "Brazilian GAAP", "Korean GAAP", "UK GAAP")

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
              r"generally\s+accepted\s+accounting\s+principles\s+(?:in|of)\s+the\s+United\s+States|\bU\.?\s?S\.?\s+GAAP\b|"
              # CEMIG's 2009 opinion: "in conformity with U.S. generally accepted accounting principles".
              r"(?:\bU\.?\s?S\.?|United\s+States)\s+generally\s+accepted\s+accounting\s+principles"),
    (CANADIAN_GAAP, r"accounting\s+principles\s+generally\s+accepted\s+in\s+Canada|Canadian\s+generally\s+accepted|"
                    r"\bCanadian\s+GAAP\b"),
    (DUTCH_GAAP, r"accounting\s+principles\s+generally\s+accepted\s+in\s+the\s+Netherlands|\bDutch\s+GAAP\b"),
    # The home bases of foreign filers before IFRS, as their auditors name them; each opinion goes on to say how it
    # differs from U.S. GAAP, which is not the basis (Televisa's "in conformity with Mexican Financial Reporting
    # Standards. Mexican Financial Reporting Standards vary in certain significant respects from U.S. generally accepted
    # accounting principles").
    (MEXICAN_FRS, r"Mexican\s+Financial\s+Reporting\s+Standards|\bMexican\s+FRS\b|\bMFRS\b"),
    (MEXICAN_GAAP, r"accounting\s+principles\s+generally\s+accepted\s+in\s+Mexico|\bMexican\s+GAAP\b"),
    (CHILEAN_GAAP, r"accounting\s+principles\s+generally\s+accepted\s+in\s+Chile|\bChilean\s+GAAP\b"),
    # Brazil's own practices, unless the same words say the statements are also on IFRS (Brazilian filers' statements
    # since 2010: "in accordance with accounting practices adopted in Brazil and with IFRS").
    (BRAZILIAN_GAAP, r"(?:accounting\s+practices\s+adopted\s+in\s+Brazil|Brazilian\s+accounting\s+(?:practices|principles)|"
                     r"(?:generally\s+accepted\s+)?accounting\s+principles\s+(?:generally\s+accepted\s+)?in\s+Brazil|"
                     r"\bBrazilian\s+GAAP\b|\bBR\s+GAAP\b|"
                     # Sabesp's Q3 2009 ITR review: "stated in accordance with the standards issued by the Brazilian
                     # Securities Commission (CVM) applicable to the preparation of Quarterly Information".
                     r"standards\s+issued\s+by\s+the\s+Brazilian\s+Securities\s+(?:and\s+Exchange\s+)?Commission)"
                     r"(?![^.;]{0,60}?\b(?:and|or|as\s+well\s+as)\b[^.;]{0,80}?(?:IFRS|International\s+Financial\s+Reporting))"
                     # nor where the words are about the parent company's own statements: "the individual financial
                     # statements … in accordance with accounting practices adopted in Brazil. In the case of the
                     # Company, those practices differ from the IFRS applicable to separate financial statements"
                     # (Ambev's 2013 report); "not defined under BR GAAP or IFRS" (Petrobras' releases) names neither.
                     r"(?!.{0,80}?differ\w*\s+from\s+(?:the\s+)?IFRS\s+applicable\s+to\s+separate)"),
    # KEPCO's "the Financial Accounting Standards as established by the Financial Supervisory Commission of the Republic
    # of Korea"; Korea's IFRS ("Korean International Financial Reporting Standards") is IFRS.
    (KOREAN_GAAP, r"accounting\s+principles\s+generally\s+accepted\s+in\s+(?:the\s+Republic\s+of\s+)?Korea|"
                  r"financial\s+accounting\s+standards[^.;]{0,80}?(?:in|of)\s+the\s+Republic\s+of\s+Korea|"
                  r"\bKorean\s+GAAP\b|\bK-GAAP\b"),
    (UK_GAAP, r"accounting\s+principles\s+generally\s+accepted\s+in\s+the\s+United\s+Kingdom|\bU\.?K\.?\s+GAAP\b"),
    # ASML's statutory interim reports: "prepared in accordance with IAS 34", IFRS's standard for interim reports.
    # CEMEX's 2016 opinion: "International Reporting Standards as issued by the International Accounting Standards Board".
    # Gerdau's 2010 releases: "the international accounting practices issued by the International Accounting Standards
    # Board (IASB) and the accounting practices adopted in Brazil".
    # Korea's adoption: "Korean International Financial Reporting Standards", "K-IFRS" (KEPCO's 20-Fs, OpenDART).
    (K_IFRS, r"\bKorean\s+(?:International\s+Financial\s+Reporting\s+Standards|IFRSs?)\b|\bK-IFRSs?\b"),
    # Brazil's: IFRS or IAS 34 beside the CPC's pronouncements ("CPC 21 (R1) and IAS 34"), unless the words say IFRS as
    # issued by the IASB too (_named).
    (CPC_IFRS, r"(?:\bCPC\b|Accounting\s+Pronouncements\s+Committee|Comit[eê]\s+de\s+Pronunciamentos)[^.;]{0,160}?"
               r"(?:\bIFRSs?\b|\bIAS\s*\d+\b|International\s+Financial\s+Reporting)|(?:\bIFRSs?\b|\bIAS\s*\d+\b|International\s+"
               r"Financial\s+Reporting\s+Standards)[^.;]{0,160}?(?:\bCPC\b|Accounting\s+Pronouncements\s+Committee)"),
    (IFRS, r"International\s+(?:Financial\s+)?Reporting\s+Standards|\bIFRSs?\b|\bIAS\s*34\b|"
           r"international\s+accounting\s+(?:practices|standards)\s+issued\s+by\s+the\s+International\s+Accounting\s+Standards\s+Board"),
)
_ANY_NAME = re.compile("|".join(f"(?P<b{index}>{pattern})" for index, (_basis, pattern) in enumerate(_NAMES)), re.I)

# The auditors' opinion: "present fairly, in all material respects, … in conformity with …".
# A review's conclusion: "we are not aware of any material modifications … for them to be in conformity with …".
# Braskem's 2022 opinion leaves out the "with": "… in conformity International Financial Reporting Standards".
_OPINION = re.compile(r"(?:present\s+fairly|not\s+aware\s+of\s+any\s+material\s+modifications)\b.{0,600}?"
                      r"\b(?:in\s+conformity(?:\s+with)?|in\s+accordance\s+with)\b(.{0,500})", re.I | re.S)
# A release or a statement's own note: "All figures are based on US GAAP", "prepared in accordance with IFRS", FEMSA's
# "announced today its operational and financial results for the first quarter of 2012 under International Financial
# Reporting Standards".
_STATED = re.compile(r"\b(?:based\s+on|prepared\s+(?:in\s+accordance\s+with|in\s+conformity\s+with|under|on\s+the\s+basis\s+of)|"
                     r"in\s+(?:conformity|accordance)\s+with|results\s+(?:for\s+[^.;]{0,60}?\s+)?under)\b(.{0,300})", re.I | re.S)
# A basis the company used before, not this document's: Lloyds' 2005 interim report, "Up to 31 December 2004 the Group
# prepared its financial statements in accordance with UK Generally Accepted Accounting Principles (UK GAAP). On 1
# January 2005 the Group implemented International Financial Reporting Standards".
_FORMER = re.compile(r"\b(?:up\s+to|until|prior\s+to)\b[^.;]{0,90}$", re.I)
# The 20-F cover's question, "Indicate by check mark which basis of accounting the registrant has used to prepare the
# financial statements included in this filing: U.S. GAAP o International Financial Reporting Standards as issued by
# the International Accounting Standards Board þ Other o", each box beside its basis.
_COVER_BOXES = re.compile(r"basis\s+of\s+accounting\s+the\s+registrant\s+has\s+used[^:]{0,120}:?(.{0,400}?)(?:\bIf\b|$)", re.I)
_BOX = re.compile(r"(U\.?\s?S\.?\s+GAAP)|(International\s+Financial\s+Reporting\s+Standards)|(\bOther\b)|"
                  r"(?<!\S)([þýxX☒■✓✔☑]|[oq¨☐□])(?!\S)", re.I)
_TICKED = set("þýxX☒■✓✔☑")


# A 20-F's cover asks every filer the same question, whatever its basis: "If an emerging growth company that prepares
# its financial statements in accordance with U.S. GAAP, indicate by check mark …".
_COVER = re.compile(r"\bemerging\s+growth\s+company\s+that\s+prepares\s+its\s+financial\s+statements\s+in\s+accordance\s+"
                    r"with\s+U\.?\s?S\.?\s+GAAP", re.I)


def _named(text: str) -> str | None:
    match = _ANY_NAME.search(text)
    if match is None:
        return None
    index = int(next(name for name, value in match.groupdict().items() if value is not None)[1:])
    if _NAMES[index][0] == CPC_IFRS and _IASB.search(text[max(0, match.start() - 160):match.end() + 160]):
        return IFRS  # IFRS as issued by the IASB and the CPC's pronouncements both: IFRS
    return _NAMES[index][0]


def stated_basis(text: str) -> str | None:
    """The basis a document's statements are prepared on: the auditors' opinion's, else the box a 20-F's cover ticks,
    else the first basis a sentence says the figures are prepared on or based on."""
    text = re.sub(r"<[^>]+>", " ", text or "")  # HTML read as its words
    text = re.sub(r"&(nbsp|#160|#xa0);", " ", text, flags=re.I)
    text = _COVER.sub(" ", re.sub(r"\s+", " ", text))
    # The group's opinion before the parent's own: Braskem's 2017 statements open with the individual statements'
    # "Accounting Practices Adopted in Brazil", then the consolidated statements' IFRS (round 85).
    opinions, position = [], 0
    while (match := _OPINION.search(text, position)) is not None:
        position = match.start(1)  # the next opinion may sit in this one's words
        if (basis := _named(match.group(1))) is not None:
            opinions.append((basis, "consolidated" in match.group(0)[:match.start(1) - match.start()].lower()))
    if opinions:
        return next((basis for basis, group in opinions if group), opinions[0][0])
    boxed = _cover_basis(text)
    if boxed is not None:
        return boxed
    position = 0
    while (match := _STATED.search(text, position)) is not None:
        position = match.end()
        if _FORMER.search(text[max(0, match.start() - 120):match.start()]):
            position = match.start(1)  # the words after a former basis are read again: the next sentence may state it
            continue
        basis = _named(match.group(1)[:200])
        if basis is not None:
            return basis
    return None


def _cover_basis(text: str) -> str | None:
    """The basis a 20-F's cover ticks, where one box of U.S. GAAP and IFRS is ticked; the boxes print after their
    bases or before them."""
    match = _COVER_BOXES.search(text)
    if match is None:
        return None
    tokens = [(US_GAAP if found.group(1) else IFRS if found.group(2) else "other" if found.group(3) else None, found.group(4))
              for found in _BOX.finditer(html.unescape(match.group(1)))]
    if not tokens:
        return None
    after = tokens[0][0] is not None  # "U.S. GAAP o IFRS þ", else "☐ U.S. GAAP ☒ IFRS"
    ticked = []
    for index, (basis, _mark) in enumerate(tokens):
        if basis is None:
            continue
        beside = index + 1 if after else index - 1
        mark = tokens[beside][1] if 0 <= beside < len(tokens) else None
        if mark is not None and mark in _TICKED:
            ticked.append(basis)
    return ticked[0] if len(ticked) == 1 and ticked[0] != "other" else None


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
    # Embraer's "in millions of U.S.dollars": no space after the stop.
    ("USD", r"US\$|U\.S\.\$|\bUSDm?\b|U\.\s?S\.\s*dollars?|United\s+States\s+dollars?|US\s+dollars?"),
    ("CAD", r"C\$|CDN\.?\$|\bCAD\b|Canadian\s+dollars?"),  # Brookfield's 2001 releases: "(CDN.$)"
    ("HKD", r"HK\$|\bHKD\b|Hong\s+Kong\s+dollars?"),
    ("EUR", r"€|\bEURm?\b|\beuros?\b"),
    ("NLG", r"\bNLG\b|\bguilders?\b|\bNfl\.?"),
    ("GBP", r"£|\bGBPm?\b|pounds?\s+sterling"),  # Lloyds TSB's "GBPm"
    ("JPY", r"¥|\bJPY\b|\byen\b"),
    ("CNY", r"\bRMB\b|\bCNY\b|renminbi"),
    # KEPCO's 20-Fs head their columns "Won" and "U.S. dollars (Note 2)".
    ("KRW", r"₩|\bKRW\b|\bKorean\s+won\b|\bwon\b"),
    ("BRL", r"(?<![A-Z.])R\$|\bBRL\b|\bBrazilian\s+reais?\b|\breais\b"),
    ("CHF", r"\bCHF\b|\bSwiss\s+francs?\b"),
    # The other home currencies of US-listed foreign filers (Televisa's "In thousands of Mexican pesos", Enel Chile's
    # "In thousands of Chilean pesos", Andina's "ThCh$").
    ("MXN", r"\bMXN\b|\bMexican\s+pesos?\b|\bMx\$|\bMxP\b"),  # América Móvil's releases: "Millions of MxP"
    ("CLP", r"\bCLP\b|\bChilean\s+pesos?\b|\bThCh\$|\bCh\$"),
    ("ARS", r"\bARS\b|\bArgentine\s+pesos?\b"),
    ("COP", r"\bCOP\b|\bColombian\s+pesos?\b"),
    ("PEN", r"\bPEN\b|\bPeruvian\s+soles\b|\bsoles\b"),
    ("AUD", r"A\$|\bAUD\b|Australian\s+dollars?"),
    ("SGD", r"S\$|\bSGD\b|Singapore\s+dollars?"),
    ("ILS", r"\bNIS\b|\bILS\b|\bshekels?\b"),
    ("INR", r"\bINR\b|\bIndian\s+rupees?\b|\brupees?\b"),
)

# A statement printing only "$" or "dollars", or "pesos" (Alcon's "($ millions)", Petrobras' "(In millions of
# Dollars)", GAP's "(in thousands of pesos)"): the kind of currency, not which; the company's own reporting currency of
# that kind decides it (pipeline/company.py), else none.
BARE_DOLLARS, BARE_PESOS = "$", "pesos"
DOLLAR_CURRENCIES = {"USD", "CAD", "AUD", "HKD", "SGD", "TWD"}
PESO_CURRENCIES = {"MXN", "CLP", "ARS", "COP"}
_BARE_DOLLARS = re.compile(r"\(\s*\$|\$\s*(?:in\s+)?(?:millions?|thousands?|billions?|000)\b|"
                           r"\b(?:millions?|thousands?|billions?)\s+of\s+dollars\b|\bin\s+dollars\b", re.I)
_BARE_PESOS = re.compile(r"\b(?:millions?|thousands?|billions?)\s+of\s+pesos\b|\bin\s+pesos\b", re.I)


def bare_currency(text: str) -> str | None:
    """"$" or "pesos" where a text names a currency's kind only."""
    if _BARE_DOLLARS.search(text or ""):
        return BARE_DOLLARS
    if _BARE_PESOS.search(text or ""):
        return BARE_PESOS
    return None


def same_kind(bare: str, currency: str | None) -> bool:
    return currency in (DOLLAR_CURRENCIES if bare == BARE_DOLLARS else PESO_CURRENCIES)
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
    """A form's currency when nothing says otherwise: a U.S. company's dollars. A 40-F, a 20-F or a release must state
    its own: Brookfield files 40-Fs and reports in U.S. dollars, saying so only in its XBRL."""
    return "USD" if form.upper().startswith(("10-K", "10-Q")) else None
