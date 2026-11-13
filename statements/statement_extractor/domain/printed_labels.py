"""The wording printed statements use for the lines their checks and pairing rely on: totals, cash balances,
income before taxes, net income, per-share lines and share counts."""
from __future__ import annotations

import re


class Widened:
    """A pattern our rules word, which the standards' own labels can widen (pipeline/standards.py): it searches and
    matches as the pattern, and as any label given, printed whole (key lines) or within a title's words."""

    def __init__(self, pattern: re.Pattern):
        self.rules, self.compiled = pattern, pattern

    def widen(self, labels, whole: bool = True) -> None:
        from .standard_labels import phrase
        if not labels:
            return
        either = "|".join(phrase(text) for text in sorted(labels, key=len, reverse=True))
        self.compiled = re.compile(f"(?:{self.rules.pattern})|" + (f"^(?:{either})$" if whole else rf"(?<!\w)(?:{either})(?!\w)"),
                                   self.rules.flags)

    def __getattr__(self, name):
        return getattr(self.compiled, name)


# ASUR's "Earning per American Depositary Share (in U.S. Dollars) 7.1108", read at its statement's thousands until round 79.
# Copel's "Earnings (loss) per thousand outstanding shares", in reais as printed (served at the thousands scale to round 96).
PER_SHARE = re.compile(r"per\s+(common\s+|ordinary\s+|series\s+\W?\w+\W?\s*)?share|per\s+ads|per\s+american\s+depositary\s+(share|receipt)|"
                       r"per\s+(?:one\s+)?thousand\s+(?:\w+\s+){0,2}shares|"
                       r"^(basic|diluted)\b|earnings?(?:\s*\((?:loss|losses)\))?\s+per|\beps\b", re.I)


# Operating data a release prints atop its income statement, in units of its own (Coca-Cola FEMSA's "Volume (million
# unit cases)", "Transactions (million transactions)", "Average price per unit case"): no line of the statement.
OPERATING_DATA = re.compile(r"\((?:in\s+)?(?:millions?|thousands?)\s+(?:of\s+)?(?:unit\s+cases|transactions)\)|\bper\s+unit\s+case\b", re.I)


# Not money about shares: FEMSA's 2010 "Income from the exchange of shares with Heineken, net of taxes".
SHARE_COUNT = re.compile(r"^(?!(?:income|gain|loss|profit|proceeds|net\s+income)\b).*?(?:\bshares\b|weighted[-\s]average)", re.I)


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


# TIM's 6-Ks print the total on the heading itself: "Assets 55,706,609", then its lines (only rows with figures count).
ASSETS = Widened(re.compile(r"^(total\s+)?assets$", re.I))


# AMD 2009: "Total liabilities and stockholders' equity (deficit)".
# KBR 2007: "Total liabilities, minority interest and shareholders' equity and accumulated other comprehensive loss".
LIABILITIES_AND_EQUITY = Widened(re.compile(r"^(total\s+liabilities.*|liabilities\s+and\s+(stockholders|shareholders)['’´]?\s+)\b(equity|investment|deficit|capital)(\s*\(deficit\))?"
                                    r"(\s+and\s+accumulated\s+other\s+comprehensive\s+(income|loss|\(loss\)\s+income))?$|"
                                    # KEPCO: "Total Shareholders' Equity and Liabilities"; PicS's "TOTAL EQUITY AND LIABILITIES"
                                    r"^total\s+((stockholders|shareholders)['’´]?\s+)?equity\s+and\s+liabilities$", re.I))


LIABILITIES = re.compile(r"^total\s+liabilities$", re.I)


# Balance sheets whose "Total liabilities" counts equity in too, and so equals total assets: UK GAAP's (Lloyds TSB to
# 2004, shareholders' funds among the liabilities) and Brazil's CVM forms (Copel's ITRs: "Total liabilities" over
# "2.05 Stockholders' equity"). The equity section is named by a line such as these.
EQUITY_SECTION = re.compile(r"\b(stockholders|shareholders|shareowners)['’´]?\s+(equity|funds)\b", re.I)


EQUITY = re.compile(r"^total\s+((stockholders|shareholders|shareowners)['’´]?\s+)?equity(\s*\(deficit\))?$", re.I)


# Stock Yards: "Beginning cash and cash equivalents", "Ending cash and cash equivalents".
# BCB Bancorp's 2008 10-Qs: "Cash and cash equivalents-begininng" (so spelled) and "Cash and cash equivalents-ending".
# Mexican FRS (FEMSA's 20-Fs to 2011): "Initial cash and cash equivalents", "Total ending balance of cash and cash
# equivalents, net"; never its "Initial cash" before the discontinued operations' share is taken off.
# Copel's 2004 quarterly information: "Opening balance" and "Closing balance" under the change in cash.
OPENING = Widened(re.compile(r"\bcash\b.*\b(beginning|begininng|beginnng|start)\b|^(beginning|opening)\b.*\bcash\b|^opening\s+balance$|"
                     r"^initial\s+cash\s+and\s+cash\s+equivalents\b|^initial\s+balance,?\s+net\b|"
                     r"^initial\s+balance\s+of\s+cash\b", re.I))  # Coca-Cola FEMSA's FY2011 20-F
# The opening after restricted cash is taken off (Coca-Cola FEMSA's 20-Fs to 2011): "Initial cash and cash equivalents
# 6,192", "Initial restricted cash (391)", "Initial balance, net 5,801".
NET_OPENING = re.compile(r"^initial\s+balance,?\s+net\b", re.I)
# FEMSA's "Ending balance" before the discontinued operations' cash is taken off ("Total ending balance … net").
GROSS_CLOSING = re.compile(r"^ending\s+balance$", re.I)
# A UK GAAP cash flow statement's (FRS 1) line before its financing section: "Net cash inflow (outflow) before financing".
BEFORE_FINANCING = re.compile(r"^net\s+cash\s+(?:inflow|outflow)[^:]*\bbefore\s+financing$", re.I)


CLOSING = Widened(re.compile(r"\bcash\b.*\b(end|ending)\b|^(total\s+)?(ending|closing)\b.*\bcash\b|^closing\s+balance$", re.I))


# Not Brookfield's "Net change in non-cash working capital balances", nor an exchange line ("Effects of exchange rate
# differences on cash"); UBS's "Net cash flow from / (used in) operating, investing and financing activities" is one.
CHANGE = re.compile(r"\b(increase|decrease|change).*(?<!non-)(?<!non\s)\bcash\b|(?<!non-)\bcash\b.*\b(increase|decrease)|"
                    r"^total\s+cash\s+(generated|provided|used)|"
                    r"^net\s+cash\s+flows?\b.*\boperating,\s+investing,?\s+and\s+financing\s+activities$|"
                    r"^net\s+cash\s+flows?$", re.I)  # Mexican FRS (FEMSA's 20-Fs to 2011)


EXCHANGE = re.compile(r"exchange\s+rate|foreign\s+currency|currency\s+translation|foreign\s+exchange", re.I)  # Brookfield: "Foreign exchange revaluation"


# EOG 2000: "NET OPERATING CASH INFLOWS", "NET INVESTING CASH OUTFLOWS", "NET FINANCING CASH OUTFLOWS".
SECTIONS = re.compile(r"(operating|investing|financing)\s+activities(\s+(from|of)\s+continuing\s+operations)?$|"
                      r"^net\s+(operating|investing|financing)\s+cash\s+(in|out)?flows?$", re.I)


# Goldman Sachs: "Pre-tax earnings".
# Scotiabank: "Income before the undernoted" (the taxes and minority interest printed under it).
PRETAX = Widened(re.compile(r"before\b.{0,50}?\b(income\s+)?tax(es|ation)?\b|^profit\s+before\s+tax|^pre-?\s?tax\s+(earnings|income|profit)\b|"
                    r"^income\s+before\s+the\s+undernoted\b",
                    re.I))


# Kroger 2009: "Net earnings including noncontrolling interests", then the parent's share.
NET_INCOME = Widened(re.compile(r"^net\s+(income|earnings|loss)(\s+\(loss\))?$|^net\s+profit(\s*/\s*\(loss\)|\s+\(loss\))?$|"
                        # IFRS: TIM's "Net profit for the period", Petrobras's "Net income for the period"
                        r"^net\s+(income|profit|earnings|loss)(\s*/\s*\(loss\)|\s+\(loss\))?\s+for\s+the\s+(period|year|quarter)$|^net\s+\((loss|income)\)\s*/?\s*(income|earnings|loss)$|^net\s+income\s+\(loss\)\s+attributable\s+to\b|"
                        r"^net\s+(income|earnings)(\s+\(loss\))?\s+including\s+non-?controlling\s+interests?$|"
                        # Canadian GAAP from 2007, no other comprehensive income: Gildan's "Net earnings and comprehensive income".
                        r"^net\s+(income|earnings)(\s+\(loss\))?\s+and\s+comprehensive\s+(income|earnings)(\s+\(loss\))?$|"
                        # Salesforce 2009: "Consolidated net income", then "Net income attributable to salesforce.com".
                        r"^(consolidated\s+net|net\s+consolidated)\s+(income|earnings)(\s+\(loss\))?$|"  # FEMSA 2010: "Net consolidated income"
                         r"^income\s+\(loss\)\s+after\s+taxes$|"  # Copel's 2003 quarterly information
                         r"^net\s+income\s+attributable\s+to\s+(shareholders|stockholders)\s+of\s+the\s+parent|^(\(loss\)\s+)?(profit|loss(es)?|income)(\s*\(loss\)|\s*/\s*\(loss\))?\s+for\s+the\s+(year|period)$|"  # KEPCO: "Profit (loss) for the period"; Lloyds 2011: "(Loss) profit …"; Embraer: "Losses for the period"
                        # UK GAAP (Lloyds TSB): "Profit for the period attributable to shareholders", before dividends.
                        r"^profit\s+for\s+the\s+(year|period|half-year)\s+attributable\s+to\s+(equity\s+)?shareholders$|"
                        # Lloyds' quarterly updates since 2025: "Profit before tax", "Tax expense", "Profit after tax".
                        r"^(net\s+)?profit\s+(\(loss\)\s+)?after\s+tax(ation)?$", re.I))


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
SECTION_KINDS = re.compile(r"^(net|cash|total)\b.*?\b(operat|invest|financ)(ing\s+activities|ions\b|ment\s+activities|"
                           r"ing\s+cash\s+(in|out)?flows?$)", re.I)  # EOG's "NET OPERATING CASH INFLOWS"

# A section headed by its kind alone, its total unlabelled (Brascan's 2001 releases: "CONSOLIDATED CASH FLOW FROM
# OPERATIONS", "FINANCING AND SHAREHOLDER DISTRIBUTIONS", "INVESTING").
SECTION_HEADS = re.compile(r"^(?:consolidated\s+cash\s+flows?\s+from\s+)?(operat|invest|financ)(?:ing|ions)\b(?!.*\d)", re.I)

# A heading over the cash balances themselves ("CASH AND CASH EQUIVALENTS", "Cash and due from banks"), under which
# "Balance at beginning of year" names the opening cash; "Cash flows from financing activities" is not one.
CASH_HEADING = re.compile(r"^cash\s+and\s+(cash\s+equivalents|due\s+from\s+banks|temporary\s+cash\s+investments)\b", re.I)
# Adjustments printed between the change in cash and the balances: the exchange-rate effect, and others such as
# TSMC's "effect of changes in consolidated entities".
EFFECT = re.compile(r"^effects?\s+of\b", re.I)
