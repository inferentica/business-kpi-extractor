"""Statement titles and key lines as the IFRS and US GAAP taxonomies name them, in every language they carry (IFRS:
English, Spanish, Japanese, Korean; US GAAP: English), so a statement printed with the standard's own words is found
and checked though no rule of ours words it so (s4-standards, 2026-11-13). The files are fetched on the runner into the
run's cache and never kept in the repository (the IFRS Foundation limits their reproduction; domain/ifrs_labels.py).
They only widen what is recognised: a title or a line printed exactly as a taxonomy label. No figure is filled,
changed or rejected by them."""
from __future__ import annotations

import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

_IFRS = "https://xbrl.ifrs.org/taxonomy/{v}/full_ifrs/labels/lab_full_ifrs-{lang}_{v}.xml"
FILES = {f"ifrs-{lang}-{v}": _IFRS.format(v=v, lang=lang)
         for lang, v in (("en", "2024-03-27"), ("es", "2021-03-24"), ("ja", "2023-03-23"), ("ko", "2023-03-23"))}
FILES["us-gaap-en-2024"] = "https://xbrl.fasb.org/us-gaap/2024/elts/us-gaap-lab-2024.xml"
_XLINK = "{http://www.w3.org/1999/xlink}"
_ROLES = {"label", "totalLabel", "periodStartLabel", "periodEndLabel", "terseLabel"}

# The elements whose labels name each title and key line: IFRS names first, then US GAAP's.
ELEMENTS = {
    "title:income": ("IncomeStatementAbstract", "StatementOfComprehensiveIncomeAbstract"),
    "title:balance": ("StatementOfFinancialPositionAbstract",),
    "title:cash_flow": ("StatementOfCashFlowsAbstract",),
    "pretax": ("ProfitLossBeforeTax", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"),
    "net_income": ("ProfitLoss", "NetIncomeLoss"),
    "assets": ("Assets",),
    "liabilities_and_equity": ("EquityAndLiabilities", "LiabilitiesAndStockholdersEquity"),
    "cash": ("CashAndCashEquivalents", "CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
}


_STATEMENT_WORD = re.compile(r"\bstatement\b|\bestado\b|計算書|표(?:\s*\[[^\]]*\])?$", re.I)
_COMPREHENSIVE = re.compile(r"comprehensive|integral|包括|포괄", re.I)


def parse_labels(content: bytes) -> dict[str, dict[str, list[str]]]:
    """Each element's labels by role ("label", "totalLabel", "periodStartLabel", …), its namespace prefix dropped."""
    root = ET.fromstring(content)
    located, texts, found = {}, {}, {}
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "loc":
            located[element.get(_XLINK + "label")] = element.get(_XLINK + "href", "").rsplit("#", 1)[-1]
        elif tag == "label":
            role = element.get(_XLINK + "role", "").rsplit("/", 1)[-1]
            if role in _ROLES and (element.text or "").strip():
                texts.setdefault(element.get(_XLINK + "label"), []).append((role, element.text.strip()))
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "labelArc":
            name = located.get(element.get(_XLINK + "from"), "")
            for role, text in texts.get(element.get(_XLINK + "to"), []):
                found.setdefault(re.sub(r"^[a-z-]+_", "", name), {}).setdefault(role, []).append(text)
    return found


def labels_for(files: dict[str, dict]) -> dict[str, set[str]]:
    """Each title and key line's labels across the files read, "[abstract]" and the like dropped."""
    out: dict[str, set[str]] = {}
    for key, names in ELEMENTS.items():
        for labels in files.values():
            for name in names:
                for role, texts in labels.get(name, {}).items():
                    if (key == "cash") != (role in ("periodStartLabel", "periodEndLabel")):
                        continue  # cash's opening and closing lines alone; other lines' own labels, never "… at end of period"
                    if key.startswith("title:"):
                        # A statement's name, never a phrase prose uses ("Profit or loss"), nor the statement of
                        # comprehensive income, which the finder reads apart (readers/printed/find.py).
                        texts = [text for text in texts if _STATEMENT_WORD.search(text) and not _COMPREHENSIVE.search(text)]
                    out.setdefault(f"{key}:{role}" if key == "cash" else key, set()).update(
                        re.sub(r"\s*\[[^\]]*\]\s*$", "", text) for text in texts)
    return {key: {text for text in texts if len(text) > 2} for key, texts in out.items()}


def load() -> dict[str, set[str]]:
    """The labels, from the run's cache or the taxonomies' sites; whatever cannot be read is left out."""
    folder = os.environ.get("STATEMENTS_ARCHIVE_CACHE")
    files = {}
    for name, url in FILES.items():
        kept = Path(folder, "standards", f"{name}.json") if folder else None
        try:
            if kept is not None and kept.exists():
                files[name] = json.loads(kept.read_text())
                continue
            request = urllib.request.Request(url, headers={"User-Agent": "TradeTracker statements"})
            with urllib.request.urlopen(request, timeout=120) as response:
                files[name] = parse_labels(response.read())
            if kept is not None:
                kept.parent.mkdir(parents=True, exist_ok=True)
                kept.write_text(json.dumps(files[name]))
        except (OSError, ET.ParseError, ValueError):
            continue
    return labels_for(files)


def phrase(text: str) -> str:
    """A label as a pattern: its words in order, any spacing between them."""
    return r"\s+".join(re.escape(word) for word in text.split())
