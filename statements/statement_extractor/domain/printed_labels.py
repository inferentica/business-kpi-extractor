"""The wording printed statements use for the lines their checks and pairing rely on: totals, cash balances,
income before taxes, net income, per-share lines and share counts."""
from __future__ import annotations

import re


PER_SHARE = re.compile(r"per\s+(common\s+|ordinary\s+)?share|per\s+ads|^(basic|diluted)\b|earnings\s+per|\beps\b", re.I)


SHARE_COUNT = re.compile(r"\bshares\b|weighted[-\s]average", re.I)


# Lines a statement's checks and later eras pair by: the concept XBRL gives the same line.
KNOWN = (
    (re.compile(r"^total\s+assets$", re.I), "us-gaap_Assets"),
    (re.compile(r"^total\s+liabilities\s+and\s+(stockholders|shareholders|shareowners)['’]?\s+(equity|investment)$|^total\s+liabilities\s+and\s+(total\s+)?equity$|"
                r"^total\s+liabilities,.*and\s+(stockholders|shareholders|shareowners)['’]?\s+equity$", re.I), "us-gaap_LiabilitiesAndStockholdersEquity"),
    (re.compile(r"^total\s+liabilities$", re.I), "us-gaap_Liabilities"),
    (re.compile(r"^total\s+(stockholders|shareholders|shareowners)['’]?\s+equity$", re.I), "us-gaap_StockholdersEquity"),
    (re.compile(r"^total\s+current\s+assets$", re.I), "us-gaap_AssetsCurrent"),
    (re.compile(r"^total\s+current\s+liabilities$", re.I), "us-gaap_LiabilitiesCurrent"),
    (re.compile(r"^(net|total net)\s+(sales|revenues?|operating revenues)$|^(total\s+)?revenues?$", re.I), "us-gaap_Revenues"),
    (re.compile(r"^gross\s+(margin|profit)(\s+on\s+sales)?$", re.I), "us-gaap_GrossProfit"),
    (re.compile(r"^operating\s+income(\s+\(loss\))?$|^income(\s+\(loss\))?\s+from\s+operations$", re.I), "us-gaap_OperatingIncomeLoss"),
    (re.compile(r"^net\s+(income|earnings)(\s+\(loss\))?$|^net\s+loss$", re.I), "us-gaap_NetIncomeLoss"),
    (re.compile(r"cash\s+(generated\s+by|provided\s+by|used\s+(in|for)|from)(\s+\(used\s+(in|for)\))?\s+operating\s+activities$|"
                r"operating\s+activities$", re.I), "us-gaap_NetCashProvidedByUsedInOperatingActivities"),
    (re.compile(r"investing\s+activities$", re.I), "us-gaap_NetCashProvidedByUsedInInvestingActivities"),
    (re.compile(r"financing\s+activities$", re.I), "us-gaap_NetCashProvidedByUsedInFinancingActivities"),
)


ASSETS = re.compile(r"^total\s+assets$", re.I)


# AMD 2009: "Total liabilities and stockholders' equity (deficit)".
LIABILITIES_AND_EQUITY = re.compile(r"^total\s+liabilities.*\b(equity|investment|deficit|capital)(\s*\(deficit\))?$", re.I)


LIABILITIES = re.compile(r"^total\s+liabilities$", re.I)


EQUITY = re.compile(r"^total\s+((stockholders|shareholders|shareowners)['’]?\s+)?equity(\s*\(deficit\))?$", re.I)


# Stock Yards: "Beginning cash and cash equivalents", "Ending cash and cash equivalents".
OPENING = re.compile(r"\bcash\b.*\b(beginning|start)\b|^(beginning|opening)\b.*\bcash\b", re.I)


CLOSING = re.compile(r"\bcash\b.*\bend\b|^(ending|closing)\b.*\bcash\b", re.I)


CHANGE = re.compile(r"(increase|decrease|change).*\bcash\b|\bcash\b.*(increase|decrease)|^total\s+cash\s+(generated|provided|used)", re.I)


EXCHANGE = re.compile(r"exchange\s+rate|foreign\s+currency|currency\s+translation", re.I)


SECTIONS = re.compile(r"(operating|investing|financing)\s+activities(\s+(from|of)\s+continuing\s+operations)?$", re.I)


# Goldman Sachs: "Pre-tax earnings".
PRETAX = re.compile(r"before\b.{0,50}?\b(income\s+)?tax(es|ation)?\b|^profit\s+before\s+tax|^pre-?\s?tax\s+(earnings|income|profit)\b",
                    re.I)


# Kroger 2009: "Net earnings including noncontrolling interests", then the parent's share.
NET_INCOME = re.compile(r"^net\s+(income|earnings|loss)(\s+\(loss\))?$|^net\s+income\s+\(loss\)\s+attributable\s+to\b|"
                        r"^net\s+(income|earnings)(\s+\(loss\))?\s+including\s+non-?controlling\s+interests?$|"
                        # Salesforce 2009: "Consolidated net income", then "Net income attributable to salesforce.com".
                        r"^consolidated\s+net\s+(income|earnings)(\s+\(loss\))?$|"
                         r"^net\s+income\s+attributable\s+to\s+(shareholders|stockholders)\s+of\s+the\s+parent|^profit\s+for\s+the\s+(year|period)$", re.I)


TOTAL = re.compile(r"^total\b", re.I)


# A balance sheet that closes each side with a bare "TOTAL" (TSMC's IFRS statements of financial position): the first
# is total assets, the last total liabilities and equity.
BARE_TOTAL = re.compile(r"^total$", re.I)

# Cash balances named by their dates (JPM: "Cash and due from banks at December 31, 2002 and 2001" … "at September 30,
# 2003 and 2002"): the first is the opening balance, the last the closing one.
DATED_CASH = re.compile(r"^cash\b.*\bat\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b", re.I)
# The three sections a cash flow statement must hold before its roll-forward proves anything (a statement's last page
# alone also rolls forward).
# The first section word names the section: ASML's "Net cash used in investing activities from operations" is investing.
SECTION_KINDS = re.compile(r"^(net|cash|total)\b.*?\b(operat|invest|financ)(ing\s+activities|ions\b|ment\s+activities)", re.I)

# A heading over the cash balances themselves ("CASH AND CASH EQUIVALENTS", "Cash and due from banks"), under which
# "Balance at beginning of year" names the opening cash; "Cash flows from financing activities" is not one.
CASH_HEADING = re.compile(r"^cash\s+and\s+(cash\s+equivalents|due\s+from\s+banks|temporary\s+cash\s+investments)\b", re.I)
# Adjustments printed between the change in cash and the balances: the exchange-rate effect, and others such as
# TSMC's "effect of changes in consolidated entities".
EFFECT = re.compile(r"^effects?\s+of\b", re.I)
