"""Checks accepted figures against their own filings, with no AI: for each figure, the cell or quote it was read from,
and the column, period, scale, sign, row label, place names and stated scale words around it. A flag is a reason for
a person to look, not a verdict: in three 300-figure audits the checker flagged about a tenth of the figures and every
one that turned out wrong was among them.

Run by the trial on its audit sample (eval-results/audit_flags.json), or by hand:
    python -m kpi_extractor.quality.audit_check eval-results/audit_sample.json eval-results/trial.json
"""

from __future__ import annotations

import html as _html
import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field

from ..documents.document import Document, parse_document, parse_number
from ..readers.extract import _heading, _nearest_heading, _places

MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
MONTH_NAMES = "january|february|march|april|may|june|july|august|september|october|november|december"
PER_UNIT = re.compile(r"\bper\s+(?!cent\b)\w|\$\s*/\s*\w|\bprices?\b|\byields?\b|\bARP[AU]\b|\bASP\b|\bratios?\b|\brates?\b", re.I)
FALL = re.compile(r"\b(decreas\w*|declin\w*|fell|dropped|down|lower|loss|lost|negative)\b", re.I)
OTHER_PERIOD = re.compile(r"first half|six months|nine months|year[- ]to[- ]date|\bytd\b|full[- ]year|for the year|guidance|expect|outlook|"
                          r"\btarget\b|cumulative", re.I)
STOP_WORDS = {"the", "of", "and", "a", "an", "in", "for", "by", "to", "total", "revenue", "revenues", "operating", "metrics",
              "net", "average", "number"}
SCALE_WORDS = {"thousand": 1e3, "million": 1e6, "billion": 1e9}


@dataclass
class Checked:
    figure: dict
    flags: list[str] = field(default_factory=list)
    detail: list[str] = field(default_factory=list)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP_WORDS and len(w) > 1}


class Fetcher:
    """Filings by URL, fetched once each with the SEC identity and a short pause; a few tries on a busy server."""

    def __init__(self, identity: str | None = None):
        self.identity = identity or os.environ.get("SEC_USER_AGENT", "TradeTracker admin@inferentica.com")
        self.raw: dict[str, str] = {}
        self.documents: dict[str, Document] = {}

    def html(self, url: str) -> str:
        if url not in self.raw:
            for attempt in range(4):
                try:
                    request = urllib.request.Request(url, headers={"User-Agent": self.identity})
                    self.raw[url] = urllib.request.urlopen(request, timeout=40).read().decode("utf-8", "ignore")
                    break
                except Exception:  # noqa: BLE001 - a busy server; tried again
                    if attempt == 3:
                        raise
                    time.sleep(3 * (attempt + 1))
            time.sleep(0.15)
        return self.raw[url]

    def document(self, url: str) -> Document:
        if url not in self.documents:
            self.documents[url] = parse_document(self.html(url), url)
        return self.documents[url]


def check_figure(item: dict, value_record: dict, fetcher: Fetcher) -> Checked:
    """One accepted figure against its filing. `item` is an audit-sample entry; `value_record` the stored value with
    its locator."""
    out = Checked(item)
    locator = value_record.get("locator") or {}
    value = float(item["value"])
    label = item["kpi"].split(" / ", 1)[-1]
    try:
        document = fetcher.document(item["source"])
    except Exception as error:  # noqa: BLE001
        out.flags.append(f"fetch failed: {error}")
        return out
    table_id = re.sub(r"^[A-Z]+_", "", locator.get("table") or "")
    if table_id and table_id in document.tables:
        _check_table(out, document.tables[table_id], locator, value, label, item)
    elif locator.get("quote"):
        _check_quote(out, locator, value, label, fetcher.html(item["source"]))
    else:
        out.detail.append(f"where: {item.get('where')!r}")
        out.flags.append("no locator to check")
    if item.get("unit") == "currency" and not item.get("currency"):
        out.flags.append("no currency")
    if re.search(r"\b(19|20)\d\d\b|\bFY ?\d\d\b", label):
        out.flags.append("KPI name carries a year")
    return out


def _check_table(out: Checked, table, locator: dict, value: float, label: str, item: dict) -> None:
    row_index, col = locator.get("row"), locator.get("col")
    row = table.rows[row_index] if row_index is not None and row_index < len(table.rows) else []
    cell = row[col] if col is not None and col < len(row) else None
    heads = [" | ".join(x for x in h[: (col or 0) + 2] if x) for h in table.rows[: min(row_index or 0, 5)]]
    out.detail += [f"ctx: {table.context[-110:]!r}", *[f"head: {h[:170]}" for h in heads],
                   f"row r{row_index}: {' | '.join(x for x in row if x)[:170]}", f"cell c{col}: {cell!r} scale used: {locator.get('scale')}"]
    parsed = parse_number(cell or "")
    if parsed is None:
        out.flags.append("no number in the cell")
    elif parsed.value:
        ratio = value / parsed.value
        out.detail.append(f"value/cell = {ratio:.4g}")
        if PER_UNIT.search(label) and abs(ratio) >= 1000:
            out.flags.append("per-unit KPI scaled by 1000+")
        if abs(ratio) not in (1, 1e3, 1e6, 1e9) and abs(abs(ratio) - round(abs(ratio))) > 1e-6:
            out.flags.append(f"odd ratio {ratio:.6g}")
        if ratio < 0:
            out.flags.append("sign flipped from the cell")
    own = " ".join(_heading(h, col) for h in table.rows[: min(row_index or 0, 6)] if col is not None and col < len(h)).strip()
    out.detail.append(f"column head: {own[:150]!r}")
    year = item["periodEnd"][:4]
    own_years = re.findall(r"\b(20\d\d)\b", own) + [f"20{y}" for m in re.findall(r"[1-4]q'?(\d\d)\b|q[1-4]'?(\d\d)\b|'(\d\d)\b", own.lower())
                                                    for y in m if y]
    if own_years and year not in own_years and str(int(year) + 1) not in own_years:
        out.flags.append(f"own column headed {sorted(set(own_years))}, period ends {item['periodEnd']}")
    header_text = own.lower() or " ".join(heads).lower()
    if re.search(r"six months|nine months|twelve months|year ended|years ended|ytd|year to date|january to (june|september)", header_text) \
            and not re.search(r"three months|quarter", header_text):
        out.flags.append("header names a longer period")
    months = [MONTHS.index(m[:3]) + 1 for m in re.findall(rf"\b({MONTH_NAMES})\b", own.lower())]
    months += [int(m) for m in re.findall(r"\b(\d{1,2})/\d{1,2}/\d{2,4}\b", own)]
    period_month = int(item["periodEnd"][5:7])
    if months and not any(min((m - period_month) % 12, (period_month - m) % 12) <= 1 for m in months):
        out.flags.append(f"column month {months} vs period end month {period_month}")
    row_label = row[0] if row else ""
    if _words(row_label) and not (_words(label) & _words(row_label)):
        out.flags.append(f"row label shares no word with the KPI: {row_label[:60]!r}")
    if re.fullmatch(r"\s*(?:FY\s?|fiscal\s+)?(?:19|20)\d\d\s*", row_label or ""):
        out.flags.append(f"row is a year, not a quarter: {row_label.strip()!r}")
    # A count scaled up must have its scale stated for it: in its row, its column's own heading, or the section heading
    # just above. A table's "(in thousands)" for its amounts is not enough (MP Materials' tonnages read 1,000x too big).
    if item.get("unit") == "count" and parsed is not None and parsed.value and abs(value / parsed.value) >= 1000:
        # Only true heading rows head a column: a row with a label of its own ("Revenue: (in thousands)") is a section.
        heading_cells = " ".join(_heading(h, col) for h in table.rows[: min(row_index or 0, 6)]
                                 if col is not None and col < len(h) and not re.search(r"[A-Za-z]", re.sub(r"(?i)\(?unaudited\)?", "", h[0] if h else "")))
        own_scale = re.search(r"\(\s*(?:in\s+)?(?:#\s*)?(?:thousand|million|billion|000s?|mil\.?|mn|mm|bn|bil\.?)|\b(?:thousand|million|billion)s?\b",
                              f"{' '.join(row)} {heading_cells} {_nearest_heading(table, row_index)}", re.I)
        if not own_scale:
            out.flags.append(f"count scaled {value / parsed.value:g}x with no scale stated for it in its row, column or section")
    if item.get("unit") == "currency" and locator.get("scale"):
        around = f"{table.context[-200:]} {' '.join(heads)} {' '.join(row)}".lower()
        stated = {SCALE_WORDS[w] for w in re.findall(r"\b(thousand|million|billion)s?\b", around)}
        stated |= {1e6 for _ in re.findall(r"\b(?:mn|mm)\b|\$ ?mm\b", around)} | {1e9 for _ in re.findall(r"\bbn\b", around)}
        if stated and float(locator["scale"]) not in stated and not PER_UNIT.search(label):
            out.flags.append(f"scale used {float(locator['scale']):g} but the table states {sorted(stated)}")
    if " / " in row_label and re.search(r"\((?:in )?(?:thousand|million|billion)s?\)", row_label, re.I) and (locator.get("scale") or 1) != 1:
        out.flags.append("row label names two measures with one scale")
    kpi_places = _places(label, own=True)
    if kpi_places:
        around_places = _places(f"{table.context[-200:]} {' '.join(heads)} {' '.join(row)} {own}")
        if kpi_places - around_places:
            out.flags.append(f"KPI names a place the table never names: {sorted(kpi_places - around_places)}")


def _check_quote(out: Checked, locator: dict, value: float, label: str, raw_html: str) -> None:
    quote, value_text = locator["quote"], locator.get("value_text") or ""
    out.detail += [f"quote: {quote[:200]!r}", f"value_text: {value_text!r}"]
    body = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw_html)))
    if re.sub(r"\s+", " ", quote)[:60] not in body:
        out.flags.append("quote not found verbatim in the filing")
    # The stored value must be the quoted number (times a scale word in it): a figure moved from its locator is suspect.
    parsed = parse_number(value_text) if value_text else None
    if parsed is not None and parsed.value and abs(abs(value) / abs(parsed.value) - round(abs(value) / abs(parsed.value))) > 1e-6 \
            or (parsed is not None and parsed.value and round(abs(value) / abs(parsed.value)) not in (1, 1000, 10**6, 10**9)):
        out.flags.append(f"value {value:,.6g} is not the quoted {value_text!r}")
    before = quote[: quote.find(value_text)] if value_text in quote else quote
    if value > 0 and FALL.search(before[-60:]):
        out.flags.append("fall word before a positive value")
    if PER_UNIT.search(label) and abs(value) >= 1e5:
        out.flags.append("per-unit KPI with a large value")
    if OTHER_PERIOD.search(quote) and not re.search(r"quarter|three months", quote, re.I):
        out.flags.append("quote names another period or a forecast")


def check_sample(sample: list[dict], values_by_symbol: dict[str, list[dict]], fetcher: Fetcher | None = None) -> list[Checked]:
    """Every figure of an audit sample against its filing. Values are matched to sample entries by KPI and period end."""
    fetcher = fetcher or Fetcher()
    # A Q4 and the fiscal year share a period end, so the fiscal label is part of the key.
    by_key = {(symbol, f"{v.get('group_label')} / {v.get('kpi_label')}", v.get("period_end"), f"{v.get('fiscal_period')} {v.get('fiscal_year')}"): v
              for symbol, values in values_by_symbol.items() for v in values}
    return [check_figure(item, by_key.get((item["symbol"], item["kpi"], item["periodEnd"], item.get("period")), {}), fetcher)
            for item in sample]


def render(checked: list[Checked]) -> str:
    lines = []
    for index, result in enumerate(checked):
        item = result.figure
        lines.append(f"=== {index} {item['symbol']} {item['period']} (ends {item['periodEnd']}) {item['kpi']} = {float(item['value']):,.4g} "
                     f"{item.get('unit')} {item.get('currency') or ''}")
        lines += [f"  {line}" for line in result.detail]
        lines += [f"  ! {flag}" for flag in result.flags]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        return 2
    sample = json.load(open(args[0]))
    trial = json.load(open(args[1]))
    checked = check_sample(sample, trial["values"])
    print(render(checked))
    flagged = sum(1 for result in checked if result.flags)
    print(f"\nChecker: flagged {flagged} of {len(checked)} accepted figures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
