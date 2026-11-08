"""Reads the numbers the AI pointed at."""
from __future__ import annotations

import re
from datetime import date, timedelta
from dataclasses import dataclass, field

from .ai import GroupSpec, KpiSpec, Located, Locator, Spec, TableLocator
from .checks import MIX_TOLERANCE
from .document import _DECLARED_CURRENCIES, Document, Table, TextBlock, clean, declared_currency, parse_number, word_number
from .xbrl import member_key, remove_subtotals

_CURRENCY_MARKERS = (("NT$", "TWD"), ("US$", "USD"), ("HK$", "HKD"), ("C$", "CAD"), ("A$", "AUD"), ("RMB", "CNY"),
                     ("€", "EUR"), ("£", "GBP"), ("¥", "JPY"), ("S/", "PEN"), ("R$", "BRL"), ("$", "USD"))


class LocateError(ValueError):
    pass


@dataclass
class ReadValue:
    group: GroupSpec
    kpi: KpiSpec
    value: float
    currency: str | None
    locator: dict
    notes: list[str] = field(default_factory=list)
    status: str = "verified"
    # The breakdown's total row: its reconciliation target, never stored as a part.
    is_total: bool = False


def read_values(document: Document, spec: Spec, located: Located, default_currency: str | None) -> tuple[list[ReadValue], list[str]]:
    """Every locator resolved against the document; locators that do not hold up are dropped with a note."""
    kpis = {f"{group.key}.{kpi.key}": (group, kpi) for group in spec.groups for kpi in group.kpis}
    groups = {group.key: group for group in spec.groups}
    values: list[ReadValue] = []
    problems: list[str] = []
    seen: set[str] = set()
    table_groups: set[str] = set()
    for locator in located.tables:
        group = groups.get(locator.group)
        if group is None or group.kind == "metric" or locator.group in table_groups:
            continue
        try:
            rows = _read_table(document, locator, group, default_currency)
        except LocateError as error:
            problems.append(f"{locator.group}: {error}")
            continue
        table_groups.add(locator.group)
        values.extend(rows)
    for locator in located.values:
        if locator.kpi.split(".", 1)[0] in table_groups:
            continue  # the whole table was read for this group
        if locator.kpi not in kpis or locator.kpi in seen:
            continue
        group, kpi = kpis[locator.kpi]
        try:
            if locator.table is not None and locator.table in document.tables:
                table = document.tables[locator.table]
                row_label = _row_label(table.rows[locator.row], locator.col) if locator.row < len(table.rows) else ""
                if not labels_agree(locator.row_label, row_label):
                    raise LocateError(f"row mismatch: {locator.table} r{locator.row} is {row_label!r}, not the "
                                      f"{locator.row_label!r} you named")
                # A part of a breakdown or mix pointed at a combined line (Total Markets & Securities Services for
                # Securities Services). A metric is often reported as its own total ("Total assets under management").
                if group.kind != "metric" and _combined_line(row_label, kpi.label):
                    raise LocateError(f"{locator.table} r{locator.row} is the combined line {row_label!r}, not "
                                      f"{kpi.label!r}: point at the row for {kpi.label} itself")
                if other := net_gross_mismatch(row_label, kpi.label):
                    raise LocateError(f"{locator.table} r{locator.row} is the {other} figure {row_label!r}, not {kpi.label!r}")
                row_cells = table.rows[locator.row] if locator.row < len(table.rows) else []
                first_cell = (row_cells[0] if row_cells else "").strip()  # a bare year has no letters
                if not located.annual and _YEAR_ROW.match(first_cell) and not _LONG_SPAN_KPI.search(kpi.label):
                    raise LocateError(f"{locator.table} r{locator.row} is the year {first_cell!r}, not a quarter")
            value, currency, notes = _read(document, locator, kpi, default_currency)
            if locator.table is not None and locator.table in document.tables and group.kind == "metric":
                if unnamed := _place_not_named(kpi.label, document.tables[locator.table], locator.row, locator.col):
                    notes.append(f"{PLACE_NOT_NAMED}: {unnamed}")
        except LocateError as error:
            problems.append(f"{locator.kpi}: {error}")
            continue
        seen.add(locator.kpi)
        held = SCALE_NOT_STATED in notes or any(note.startswith(PLACE_NOT_NAMED) for note in notes)
        values.append(ReadValue(group, kpi, value, currency, _locator_record(document, locator), notes,
                                status="needs_review" if held else "verified"))
    # One gate for every reading (replay, Flash, the reasoning reader): the column's own header outranks a period the AI states.
    kept = []
    for item in values:
        mismatch = column_duration_problem(str(item.locator.get("header") or ""), located.annual)
        if mismatch:
            problems.append(f"{item.group.key}.{item.kpi.key}: {mismatch}")
        else:
            kept.append(item)
    return kept, problems


# A week-based quarter is 12, 13, 14 or 16 weeks (PepsiCo, Costco, Kroger); its half and three quarters 24 to 28 and
# 36 to 40 weeks.
_QUARTER = re.compile(r"<3 months>|three months|<1[2-6] weeks>|(twelve|thirteen|fourteen|sixteen) weeks|quarter|\bq[1-4]\b|\b[1-4]q\b")
_LONGER = re.compile(r"<(6|9|12) months>|(six|nine|twelve) months|<(2[4-8]|3[6-9]|40|5[23]) weeks>|"
                     r"(twenty-four|twenty-six|twenty-eight|thirty-six|thirty-nine|forty|fifty-two|fifty-three) weeks|"
                     r"years? ended|year to date|fiscal years?")


def column_duration_problem(header: str, annual: bool) -> str | None:
    """Why a column is not the period being read, judged from its header alone: a six-, nine- or twelve-month column
    when a quarter is expected, or a quarter's column when the year is. Silent headers pass."""
    quarter, longer = bool(_QUARTER.search(header)), bool(_LONGER.search(header))
    if not annual and longer and not quarter:
        return f"the column is headed {header!r}, not a quarter"
    if annual and quarter and not longer:
        return f"the column is headed {header!r}, not the year"
    return None


_PEOPLE = re.compile(r"employee|headcount|workforce|staff", re.I)


def _read(document: Document, locator: Locator, kpi: KpiSpec, default_currency: str | None) -> tuple[float, str | None, list[str]]:
    value, currency, notes = _read_raw(document, locator, kpi, default_currency)
    if kpi.unit == "count" and _PEOPLE.search(kpi.label) and value > 5_000_000:
        raise LocateError(f"{kpi.label} of {value:,.0f} is not a plausible headcount")
    return value, currency, notes


def _read_raw(document: Document, locator: Locator, kpi: KpiSpec, default_currency: str | None) -> tuple[float, str | None, list[str]]:
    notes: list[str] = []
    if locator.table is not None:
        table = document.tables.get(locator.table)
        if table is None:
            raise LocateError(f"unknown table {locator.table}")
        text = table.cell(locator.row, locator.col)
        parsed = parse_number(text)
        if parsed is None:
            raise LocateError(f"no number in {locator.table} r{locator.row} c{locator.col}")
        if kpi.unit in ("currency", "count") and parsed.percent:
            raise LocateError(f"{text!r} is a percentage, not an amount")
        header = " ".join(" ".join(row) for row in table.rows[:3])
        if kpi.unit == "percent" and not parsed.percent and not re.search(
                r"%|\bpercent", f"{' '.join(table.rows[locator.row])} {header} {_section_heading(table, locator.row)}", re.I):
            raise LocateError(f"{text!r} is not a percentage")
        value = parsed.value
        if kpi.unit == "count" and parsed.scale_word is None:
            # A table's "(in millions, except employee data)" describes its amounts, not its counts: a count takes a
            # scale only from its own row label ("Paid memberships (in millions)"), its column's ("Purchase Trans.
            # (Mil.)"), or a scale the reading quotes from the document.
            # FedEx's "Avg. daily package volume (ADV) (000s):" heads the rows under it: a section heading's scale is theirs.
            row_scale = (_row_scale(" ".join(table.rows[locator.row])) or _column_scale(table, locator.row, locator.col)
                         or _row_scale(_section_heading(table, locator.row)))
            if row_scale is None and locator.scale in (1e3, 1e6, 1e9):
                if _scale_quoted(document, table, locator.scale_text, locator.scale):
                    row_scale = locator.scale
                    notes.append(f"scale quoted: {clean(locator.scale_text or '')[:40]!r}")
                else:
                    notes.append(SCALE_NOT_STATED)
                    row_scale = locator.scale
            value *= row_scale or 1
            return value, None, notes
        if kpi.unit == "currency" and parsed.scale_word is None:
            # A row that states its own scale overrides the table's: JPM's "Debit and credit card sales volume (in
            # billions)" in a table "(in millions)".
            own = _row_scale(" ".join(table.rows[locator.row])) or _column_scale(table, locator.row, locator.col) \
                or (1.0 if _PER_UNIT_KPI.search(kpi.label) else None)
            declared = own or table.declared_scale()
            if declared is None:
                declared = document.default_scale()
            if own is None and declared is not None and locator.scale in (1e3, 1e6, 1e9) and locator.scale != declared \
                    and _scale_quoted(document, table, locator.scale_text, locator.scale):
                # Wording the reader quotes from beside the table beats a caption the table only inherits from the
                # one before it: Goldman's "$ in billions" assets under supervision after its VaR table "$ in millions".
                notes.append(f"scale quoted: {clean(locator.scale_text or '')[:40]!r} over the table's {declared:g}")
                value *= locator.scale
            elif declared is not None:
                if locator.scale not in (None, declared):
                    notes.append(f"table declares scale {declared:g}, AI said {locator.scale:g}")
                value *= declared
            elif locator.scale in (1, 1e3, 1e6, 1e9):
                value *= locator.scale
                notes.extend(_ai_scale_notes(document, table, locator.scale_text, locator.scale, value))
            else:
                raise LocateError("amount scale unknown")
        context = f"{table.context} {' '.join(' '.join(row) for row in table.rows[:4])}"
        return value, _column_currency(table, locator.row, locator.col, text) or _currency(text, kpi, default_currency, context), notes
    block = document.blocks.get(locator.block or "") or _table_as_text(document, locator.block)
    if block is None:
        raise LocateError(f"unknown text block {locator.block}")
    quote, value_text = clean(locator.quote or ""), clean(locator.value_text or "")
    if quote not in block.text:
        raise LocateError("quote not found in the document")
    if value_text not in quote:
        raise LocateError("value_text is not inside the quote")
    parsed = parse_number(value_text) or (word_number(value_text) if kpi.unit == "count" else None)
    if parsed is None:
        raise LocateError(f"no number in {value_text!r}")
    if kpi.unit == "percent" and not parsed.percent and not re.search(r"\bpercent\b", quote, re.I):
        raise LocateError(f"{value_text!r} is not a percentage")
    if kpi.unit in ("currency", "count") and parsed.percent:
        # TSMC's results-day slide gives node shares ("0.15/0.18um 2%"), never amounts: a share is not this KPI.
        raise LocateError(f"{value_text!r} is a percentage, not an amount")
    value = parsed.value
    before = quote[:quote.find(value_text)]
    if forward_looking(before):
        raise LocateError("the quote is a forecast or guidance, not the reported figure")
    # "YTD 2025, the company was awarded 14 new contract commitments" is the year to date, not the quarter's count,
    # unless the KPI itself is defined over the longer span (Tesla's cumulative deliveries, PGIM's trailing flows).
    if (span := _OTHER_PERIOD.search(before[-120:])) and not _LONG_SPAN_KPI.search(kpi.label):
        # GE Vernova's "backlog increased $6.5 billion year-to-date, to approximately $26 billion" states a change over
        # the year and then the level at quarter end: the level of a stock measure is the quarter's figure.
        between = before[-120:][span.end():]
        level = re.search(r"\b(?:to|reached|reaching|stood at|stands at|as of|at the end|ended the quarter with|now)\b", between)
        if not (level and _STOCK_KPI.search(kpi.label)):
            raise LocateError(f"the quote gives the figure {span.group(0).lower()!r}, not the quarter's")
    if other := net_gross_mismatch(before[-80:], kpi.label):
        raise LocateError(f"the quote gives the {other} figure, not {kpi.label!r}")
    if value > 0 and (kpi.unit == "percent" or (kpi.unit == "count" and _CHANGE_KPI.search(kpi.label))) \
            and falls(quote, value_text):
        # "The average price per ad decreased by 16%" is a change of -16%, and Vodafone's "contract customer base
        # declined by 46,000" is net additions of -46,000: the sentence carries the sign, not the number.
        value = -value
        notes.append("a fall, as the quote says")
    if kpi.unit == "count" and parsed.scale_word is None:
        # A count takes its label's own scale, as a table row's does: "Starlink Subscribers (#, in millions) 12.0".
        value *= _row_scale(quote[:quote.find(value_text)]) or 1
    if kpi.unit == "currency" and parsed.scale_word is None:
        # The last scale heading before the quote in its block: Apollo's highlights page heads its earnings "($ in
        # millions …)" and, further on, its AUM and business drivers "($ in billions)".
        # A per-unit amount keeps its units, as in a table: UTI's "$7,300" revenue per student under "($ in millions,
        # except revenue per student amounts)".
        declared = 1.0 if _PER_UNIT_KPI.search(kpi.label) else \
            _preceding_scale(block.text, block.text.find(quote)) or block.declared_scale() or document.default_scale()
        if declared is None and locator.scale in (1, 1e3, 1e6, 1e9):
            declared = locator.scale
            notes.extend(_ai_scale_notes(document, block, locator.scale_text, locator.scale, value * declared))
        if declared is not None:
            value *= declared
        elif value < 1_000:
            raise LocateError(f"{value_text!r} has no amount scale")
    return value, _currency(f"{value_text} {quote}", kpi, default_currency, block.text), notes


# A count that is a change over the quarter (net additions, net change), which a fall makes negative.
_CHANGE_KPI = re.compile(r"\bnet\s+(?:additions?|adds|change|gains?|increase)\b|\bchange\b", re.I)


# A fall named just before the number: "decreased by 16%", "a decrease of 1%", "down 5%", "the decline on a
# constant-dollar basis was 5%". Only words may stand between, so "decreased by 3% and margin was 40%" is no fall for
# the 40%, and a level ("declined to 30%", "fell from 32% to 30%") never is.
_FALL = re.compile(r"\b(?:decreas\w*|declin\w*|fell|falls?|falling|dropped|drops?|down|lower|contracted|reduc\w*)"
                   r"(?:\s+(?!to\b|from\b)[A-Za-z][\w-]*){0,6}?\s*(?:\b(?:by|of|was|were)\s*)?$", re.I)


# A statement about the future: "We expect revenue from AI to be $12 billion" (Broadcom's guidance, once read as its
# quarter). Not "above our forecast" after a figure, nor an RPO "expected to be recognized over the next 12 months".
_FORWARD = re.compile(r"\b(?:we|management|the company)\s+(?:now\s+)?(?:expects?|anticipates?|forecasts?|projects?|plans?|targets?|guides?)\b"
                      r"|\bguidance\b|\boutlook\b|\bwill be\b|\b(?:is|are) expected to\b(?! be recogni)", re.I)
# A figure for another period than the quarter: "for the first six months of 2023", "for fiscal year 2024".
_OTHER_PERIOD = re.compile(r"\bfirst (?:six|nine) months\b|\b(?:six|nine|twelve) months ended\b|\bfor the (?:full )?(?:fiscal )?year\b"
                           r"|\bfull[- ]year\b|\bfiscal (?:year )?\d{4}\b|\byear[- ]to[- ]date\b|\bYTD\b|\bfirst half\b|\bH1\b", re.I)
# A KPI defined over a longer span than the quarter, which such wording describes rather than contradicts.
_LONG_SPAN_KPI = re.compile(r"year[- ]to[- ]date|\bYTD\b|cumulative|all[- ]time|trailing|\bTTM\b|\bLTM\b|twelve months|full[- ]year|annual|"
                            r"current[- ]year|first[- ]half|\bH1\b|since (?:inception|launch)|to date", re.I)
# A KPI that is a level at period end (a stock), not a flow over it: a year-to-date change can lead to its level.
_STOCK_KPI = re.compile(r"backlog|obligations?|\bRPO\b|\bcRPO\b|customers?|subscribers?|members?|users?|base\b|balances?|assets|"
                        r"\bAU[MSA]\b|in force|count\b|sites|rigs|fleet|stores|locations|employees|headcount|deposits|loans|capacity|"
                        r"properties|units in operation|policies", re.I)
# A table row that is a year, not a quarter: Sun Communities' operating statistics by year (2025, 2024, 2023).
_YEAR_ROW = re.compile(r"^\s*(?:FY\s?|fiscal\s+)?(?:19|20)\d\d\s*$", re.I)
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


def net_gross_mismatch(text: str, kpi_label: str) -> str | None:
    """"net" when the text names a net figure and the KPI a gross one (KKR's Net Unrealized Carried Interest is not its
    Gross Unrealized Carried Interest), "gross" the other way round, else None."""
    named = {word for word in ("net", "gross") if re.search(rf"\b{word}\b", text, re.I)}
    wanted = {word for word in ("net", "gross") if re.search(rf"\b{word}\b", kpi_label, re.I)}
    if len(wanted) == 1 and len(named) == 1 and named != wanted:
        return named.pop()
    return None


def _table_as_text(document: Document, key: str | None) -> TextBlock | None:
    """A table quoted as text: a release's highlight bullets laid out as one-row tables ("•" | "Family daily active
    people (DAP) – DAP was 2.26 billion…", Meta 2017–2020) are sentences, and a quote of one is read like a block's."""
    table = document.tables.get(key or "")
    if table is None:
        return None
    return TextBlock(table.id, clean(" ".join(cell for row in table.rows for cell in row if cell)))


def _identity(text: str) -> str:
    """A label or key reduced to what it names: "3-nanometer", "3nm" and "nm3" are one node; "Net product sales" and
    "net_product_sales" one line."""
    key = member_key(text.replace("_", " "))
    key = re.sub(r"nanometers?", "nm", key)
    return re.sub(r"^nm(\d+)$", r"\1nm", key)


def _listed_kpi(group: GroupSpec, label: str) -> KpiSpec | None:
    """The KPI on the company's list a table row is, so a whole-table reading and a row-by-row reading of one table use
    one key (both readings then agree, and a breakdown is never stored twice under two names)."""
    wanted = _identity(label)
    listed = [kpi for kpi in group.kpis if kpi.key != group.total_kpi]
    matches = [kpi for kpi in listed if wanted in (_identity(kpi.label), _identity(kpi.key))]
    if not matches:
        # A row label that adds the business's name to the listed one ("UnitedHealthcare Employer & Individual -
        # Domestic" for "Employer & Individual - Domestic"): its ending, when that is specific and names one KPI.
        # Not after a joining word: "Markets & Securities Services" is a combined line, not Securities Services.
        matches = [kpi for kpi in listed if len(_identity(kpi.label)) >= 8 and wanted.endswith(_identity(kpi.label))
                   and not re.search(r"(?:&|\band|\+|,|/)\s*$", label[:len(label) - len(_tail(label, kpi.label))], re.I)]
    if not matches and (bare := _SPACED_FOOTNOTE.sub("", label)) != label:
        # A footnote mark set apart from the label ("Net system sales 3", ASML's 2017 releases) when the label without
        # it is a listed KPI: never a name's own number ("Tier 1"), which would have no listed KPI to match.
        return _listed_kpi(group, bare)
    return matches[0] if len(matches) == 1 else None


_SPACED_FOOTNOTE = re.compile(r"\s+\d{1,2}$")
_TOTAL_LABEL = re.compile(r"\s*total\b", re.I)


def _combined_line(row_label: str, kpi_label: str) -> bool:
    """A row that ends with a KPI's name but adds a total or a joined part ("Total Markets & Securities Services" for
    Securities Services): a subtotal that contains the KPI, not the KPI."""
    tail = _tail(row_label, kpi_label)
    if not tail or _identity(row_label) == _identity(kpi_label):
        return False
    prefix = row_label[:len(row_label) - len(tail)]
    return bool(re.search(r"(?:&|\band|\+|,|/)\s*$", prefix, re.I) or re.match(r"\s*total\b", row_label, re.I))


def _tail(label: str, listed: str) -> str:
    """The end of a row label that spells the listed name, with the same letters and digits ("Employer & Individual -
    Domestic" at the end of "UnitedHealthcare Employer & Individual - Domestic")."""
    wanted = _identity(listed)
    for start in range(len(label)):
        if _identity(label[start:]) == wanted:
            return label[start:]
    return ""


def row_key(label: str) -> str:
    """A table row's KPI key: its label normalized the same way across filings ("3-nanometer" → "r3nanometer")."""
    key = member_key(label)
    return (key if key[:1].isalpha() else f"r{key}")[:48] or "row"


# A row label's own scale: "(in millions)", "(millions)", or within a longer note ("Starlink Subscribers (#, in millions,
# as of period end)", SpaceX).
_ROW_SCALE = re.compile(r"\((?:in\s+)?(?P<word>thousands|millions|billions)\)|\([^()]*\bin\s+(?P<within>thousands|millions|billions)\b[^()]*\)", re.I)


# A row priced per unit ("Starlink consumer ARPU ($ per month)") in a table "in millions (except where noted)" is in units.
_PER_UNIT = re.compile(r"\([^()]*\bper\s+(?:month|year|quarter|day|user|subscriber|member|customer|share|unit|system)\b[^()]*\)", re.I)


def _preceding_scale(text: str, at: int) -> float | None:
    """The scale of the last heading like "($ in billions)" before position `at` of a text."""
    # "($ in millions, except per share amounts)" is a scale heading too: a price per unit names no millions.
    headings = list(_ROW_SCALE.finditer(text[:max(at, 0)])) + list(_BARE_SCALE.finditer(text[:max(at, 0)]))
    if not headings:
        return None
    last = max(headings, key=lambda match: match.start())
    groups = last.groupdict()
    return {"thousands": 1e3, "millions": 1e6, "billions": 1e9}[(groups.get("word") or groups.get("within") or groups.get("bare")).lower()]


# A scale stated without brackets, in the words of a passage: Grifols' "In millions of euros" over its breakdown.
_BARE_SCALE = re.compile(r"\b(?:in|amounts in)\s+(?P<bare>thousands|millions|billions)\s+of\s+(?:euros?|dollars?|US\$|\w+ dollars)", re.I)


# A row label's short scale, Tesla's "Active FSD Subscriptions (mil)". Never a single letter: "(b)" is a footnote.
_ROW_SCALE_SHORT = re.compile(r"\(\s*(?P<word>bil|bn|mil|mm|mn|thous)\.?\s*\)", re.I)
# A KPI priced per unit is in units, whatever its table's "$ Millions, Except as Indicated" or "In thousands, except
# yield amounts" says: FedEx's "Revenue per Package" and "Package Yield", ConocoPhillips' "Realized Price ($/MCF)". Apollo's
# "Yield" is an asset class, so a yield counts only for packages, freight, pounds, pieces, shipments, passengers or units.
_PER_UNIT_KPI = re.compile(r"\bper\s+(?!cent\b)\w|\$\s*/\s*\w|\bprices?\b|\b(?:package|freight|pound|piece|shipment|passenger|unit)\s+yields?\b|\bARP[AU]\b|\bASP\b", re.I)


def _row_scale(text: str) -> float | None:
    if _PER_UNIT.search(text):
        return 1.0
    # A label naming two measures ("Leases renewed Kilowatt / NRSF (in thousands)"): the scale after the slash is the
    # second's, so the row states none for the first. Digital Realty's 92,670 kilowatts are not 92.7 million.
    if (slash := re.search(r"\w\s+/\s+\w", text)) and (scaled := re.search(r"\(([^()]*\b(?:thousand|million|billion)s?\b[^()]*)\)", text, re.I)) \
            and scaled.start() > slash.start():
        return None
    if match := _ROW_SCALE.search(text):
        return {"thousands": 1e3, "millions": 1e6, "billions": 1e9}[(match.group("word") or match.group("within")).lower()]
    if match := _ROW_SCALE_SHORT.search(text):
        return {"bil": 1e9, "bn": 1e9, "mil": 1e6, "mm": 1e6, "mn": 1e6, "thous": 1e3}[match.group("word").lower()]
    # Auna's unit column "# (000)", a row's own "(Mil.)": the column-heading forms, in the row.
    if (short := _COLUMN_SCALE.search(text)) and (found := _scale_word(short.group("word") or short.group("letter") or short.group("bare"))):
        return found
    return None


def _read_table(document: Document, locator: TableLocator, group: GroupSpec, default_currency: str | None) -> list[ReadValue]:
    """Every row of a breakdown or mix table in the reported column, labelled by its own row label. Rows that are the
    sum of other rows (an "advanced technologies" subtotal) are dropped, so each part is counted once."""
    table = document.tables.get(locator.table)
    if table is None:
        raise LocateError(f"unknown table {locator.table}")
    for index, named in ((locator.first_row, locator.first_label), (locator.last_row, locator.last_label)):
        actual = _row_label(table.rows[index], locator.col) if 0 <= index < len(table.rows) else ""
        if not labels_agree(named, actual):
            raise LocateError(f"row mismatch: {locator.table} r{index} is {actual!r}, not the {named!r} you named")
    unit = "percent" if group.kind == "mix" else "currency"
    header = " ".join(" ".join(row) for row in table.rows[:3])
    scale = 1.0
    if unit == "currency":
        scale = _column_scale(table, locator.first_row, locator.col) or table.declared_scale() or document.default_scale()
        if scale is None and locator.scale in (1, 1e3, 1e6, 1e9):
            scale = locator.scale
            if locator.scale != 1 and not _scale_quoted(document, table, locator.scale_text, locator.scale):
                raise LocateError(f"scale {locator.scale:g} is not stated in the table: quote the words that state it in scale_text")
        if scale is None:
            raise LocateError("amount scale unknown")

    def read_row(index: int, source: Table | None = None, col: int | None = None) -> tuple[str, float, str] | None:
        source, col = source or table, locator.col if col is None else col
        text = source.cell(index, col)
        parsed = parse_number(text)
        if parsed is None or index >= len(source.rows):
            return None
        label = _row_label(source.rows[index], col)
        if unit == "percent" and not parsed.percent and "%" not in header and "percent" not in header.lower():
            raise LocateError(f"{text!r} in row {index} is not a percentage")
        own = _row_scale(" ".join(source.rows[index])) if unit == "currency" else None  # a row's own "(in billions)" wins
        value = parsed.value if parsed.scale_word or unit == "percent" else parsed.value * (own or scale)
        return label, value, text

    parts: dict[str, tuple[str, float, int]] = {}
    continued: dict[str, tuple[str, int, int]] = {}  # rows read past a page break: key → (table, row, column)
    labelled_totals: list[int] = []
    for index in range(locator.first_row, locator.last_row + 1):
        if index == locator.total_row:
            continue
        row = read_row(index)
        if row is None or not row[0]:
            continue
        if _TOTAL_LABEL.match(row[0]):
            # "Total Markets & Securities Services" is a subtotal by its own name, whatever the signs of its parts
            # (JPMorgan's includes a negative "Credit Adjustments & Other", which a sum check of positive rows misses).
            labelled_totals.append(index)
            continue
        key = member_key(row[0])
        if key not in parts:
            parts[key] = (row[0], row[1], index)
    total_index = locator.total_row
    if total_index is None and labelled_totals and labelled_totals[-1] == locator.last_row:
        total_index = labelled_totals[-1]  # the table's closing "Total" row is its total
    total_table, total_col = table, locator.col
    if unit == "percent" and total_index is None:
        # A mix's own "Total 100%" row is its total, not a share (it would make the shares add up to 200%).
        hundred = [key for key, (_label, value, _index) in parts.items() if abs(value - 100) <= 0.5]
        others = sum(value for key, (_label, value, _index) in parts.items() if key not in hundred)
        if len(hundred) == 1 and abs(others - 100) <= MIX_TOLERANCE:
            total_index = parts.pop(hundred[0])[2]
    if total_index is None and unit == "currency" and parts:
        total_index = _total_row_by_sum(table, locator, parts, read_row)
    if total_index is None and unit == "currency" and parts:
        # The rows read stop short of a total: read on, through the table's continuation after a page break (TSMC's
        # node table runs 3nm–20nm on one page, 28nm to the total on the next), to the row that is their sum.
        reached = _read_to_total(document, table, locator, parts, read_row, continued)
        if reached:
            total_table, total_index, total_col = reached
    total_row = read_row(total_index, total_table, total_col) if total_index is not None else None
    total = total_row[1] if total_row else (100.0 if unit == "percent" else None)
    kept = remove_subtotals({key: value for key, (_label, value, _index) in parts.items()}, total)
    if total and unit == "currency" and total_index is not None and abs(sum(kept.values()) - total) > abs(total) * 0.001:
        block = _block_summing_to(parts, total_index, total)
        if block:
            kept = {key: parts[key][1] for key in block}
    # A subtotal the list names as a parent (the owner's Total Optum over Optum Health, Insight and Rx) is kept beside its
    # lines: they are checked against it.
    parents = {kpi.parent for kpi in group.kpis if kpi.parent}
    for key in parents & set(parts):
        kept.setdefault(key, parts[key][1])
    # A subtotal the list names as a parent but that the table labels as a total ("Total noninterest revenue", JPMorgan's
    # 2016-2017 supplements) is that parent, not a closing total.
    named_parents = {kpi.parent for kpi in group.kpis if kpi.parent}
    for index in labelled_totals:
        row = read_row(index) if index != total_index else None
        kpi = _listed_kpi(group, row[0]) if row else None
        if kpi is not None and kpi.key in named_parents and member_key(row[0]) not in parts:
            parts[member_key(row[0])] = (row[0], row[1], index)
            kept[member_key(row[0])] = row[1]
    parent_of = _place_unlisted_rows(group, parts, kept, total)
    if len(kept) < 2:
        raise LocateError("fewer than two rows")
    context = f"{table.context} {header}"
    currency = _column_currency(table, locator.first_row, locator.col, table.cell(locator.first_row, locator.col)) if unit == "currency" else None
    currency = currency or _currency(total_row[2] if total_row else table.cell(locator.first_row, locator.col),
                         KpiSpec(key="x", label="x", unit="currency"), default_currency,
                         context if declared_currency(context) else document.declared_currency()) if unit == "currency" else None
    # What the next quarter's replay needs: the total row's label (None when the table has none) and the scale used.
    total_label = _row_label(total_table.rows[total_index], total_col)[:80] if total_row is not None else None
    # A third quarter's report also shows the nine months to date beside the quarter; Q4 is then the year less them.
    nine = _nine_month_columns(document, table, locator, parts, continued, total_table, total_index) if unit == "currency" else {}
    values = []
    for key, (label, value, index) in parts.items():
        if key not in kept:
            continue
        source_table, source_row, source_col = continued.get(key, (locator.table, index, locator.col))
        record = {"table": source_table, "row": source_row, "col": source_col, "row_label": label[:80],
                  "context": table.context[-80:], "whole_table": True, "total_row_label": total_label,
                  "scale": scale if unit == "currency" else None,
                  "header": column_header(table, locator.first_row, locator.col)}
        if key in nine and "__total__" in nine:
            record.update({"ytd": nine[key] * scale, "ytd_total": nine["__total__"] * scale})
        values.append(ReadValue(group, _listed_kpi(group, label) or KpiSpec(key=row_key(label), label=label[:80], unit=unit,
                                                                            parent=parent_of.get(key)),
                                value, currency, record))
    if total_row is not None:
        record = {"table": total_table.id, "row": total_index, "col": total_col, "row_label": "Total", "whole_table": True,
                  "header": column_header(table, locator.first_row, locator.col)}
        values.append(ReadValue(group, KpiSpec(key=group.total_kpi or "total", label="Total", unit=unit), total_row[1],
                                currency, record, is_total=True))
    return values


def _place_unlisted_rows(group: GroupSpec, parts: dict[str, tuple[str, float, int]], kept: dict[str, float],
                         total: float | None) -> dict[str, str]:
    """Rows of a whole table the list does not name, where the list's own top level already adds up to the total. A row
    inside a listed subtotal's block is one of its lines (JPMorgan's "Securities gains" among the noninterest revenue
    lines) and is placed under it. A row outside every such block can't be a missing part, since the top level is
    complete, so it is left out: JPMorgan's interest income and interest expense, which only make up net interest income.
    Returns the parent of each row placed; changes nothing when the listed rows do not add up on their own."""
    if not total or group.kind != "revenue_breakdown":
        return {}
    listed = {key: kpi for key in kept if (kpi := _listed_kpi(group, parts[key][0])) is not None}
    if not any(kpi.parent for kpi in listed.values()):
        return {}
    top = [key for key, kpi in listed.items() if not kpi.parent and kpi.key != group.total_kpi]
    if len(top) < 2 or abs(sum(kept[key] for key in top) - total) > abs(total) * 0.001:
        return {}
    blocks: dict[str, tuple[int, int]] = {}
    for key, kpi in listed.items():
        lines = [parts[other][2] for other, line in listed.items() if line.parent == kpi.key]
        if lines:
            blocks[kpi.key] = (min(lines), parts[key][2])
    placed: dict[str, str] = {}
    for key in [key for key in kept if key not in listed]:
        index = parts[key][2]
        parent = next((name for name, (first, last) in blocks.items() if min(first, last) <= index <= max(first, last)), None)
        if parent:
            placed[key] = parent
        else:
            del kept[key]
    return placed


def _raw_header(table: Table, first_data_row: int, col: int) -> str:
    return clean(" ".join(row[col] for row in table.rows[:max(first_data_row, 1)][:6] if col < len(row))).lower()


def _first_data_row(table: Table) -> int:
    """The first row with a label and a figure; a row of years ("Resolution | 2023 | 2022") is still the header."""
    def figure(cell: str) -> bool:
        return bool(cell) and parse_number(cell) is not None and not re.fullmatch(r"(19|20)\d{2}", cell.strip())
    return next((i for i, row in enumerate(table.rows) if row and re.search(r"[A-Za-z]", row[0])
                 and any(figure(cell) for cell in row[1:])), 1)


def _read_to_total(document: Document, table: Table, locator: TableLocator, parts: dict, read_row, continued: dict):
    """(table, row, column) of the total, reading on from the last row read: below it in this table, then in the next
    table when it continues this one (same column header, years included). Rows on the way join the parts; nothing is
    kept unless a row equals the sum of everything above it."""
    added: dict[str, tuple[str, float, int]] = {}
    added_from: dict[str, tuple[str, int, int]] = {}
    running = sum(value for _label, value, _index in parts.values())
    segments = [(table, locator.col, locator.last_row + 1)]
    position = document.order.index(table.id) if table.id in document.order else -1
    following = [document.tables[key] for key in document.order[position + 1:position + 4] if key in document.tables][:1]
    wanted = _raw_header(table, locator.first_row, locator.col)
    for nxt in following:
        start = _first_data_row(nxt)
        col = next((c for c in range(1, max(len(row) for row in nxt.rows)) if _raw_header(nxt, start, c) == wanted), None)
        if col is not None:
            segments.append((nxt, col, start))
    for source, col, start in segments:
        for index in range(start, min(len(source.rows), start + 30)):
            row = read_row(index, source, col)
            if row is None or not row[0]:
                continue
            if running and abs(row[1] - running) <= abs(running) * 0.001:
                for key, value in added.items():
                    parts.setdefault(key, value)
                continued.update(added_from)
                return source, index, col
            key = member_key(row[0])
            if key not in parts and key not in added:
                added[key] = (row[0], row[1], index)
                if source is not table:
                    added_from[key] = (source.id, index, col)
                running += row[1]
    return None


def _nine_month_columns(document: Document, table: Table, locator: TableLocator, parts: dict, continued: dict,
                        total_table: Table, total_index: int | None) -> dict[str, float]:
    """Each row's figure in the same year's nine-months column (raw, before scale), plus "__total__"."""
    year = re.findall(r"(?:19|20)\d{2}", _raw_header(table, locator.first_row, locator.col))

    def nine_column(source: Table) -> int | None:
        start = _first_data_row(source)
        for col in range(1, max(len(row) for row in source.rows)):
            header = _raw_header(source, start, col)
            if re.search(r"nine months|9 months", header) and (not year or year[-1] in header):
                return col
        return None

    columns = {table.id: nine_column(table)}
    found: dict[str, float] = {}
    rows = [(document.tables[continued[key][0]], continued[key][1]) if key in continued else (table, index)
            for key, (_label, _value, index) in parts.items()]
    for source, index in rows:
        col = columns.setdefault(source.id, nine_column(source))
        if col is None:
            continue
        parsed = parse_number(source.cell(index, col))
        label = _row_label(source.rows[index], col) if index < len(source.rows) else ""
        if parsed is not None and label:
            found.setdefault(member_key(label), parsed.value)
    if total_index is not None:
        col = columns.setdefault(total_table.id, nine_column(total_table))
        parsed = parse_number(total_table.cell(total_index, col)) if col is not None else None
        if parsed is not None:
            found["__total__"] = parsed.value
    return found


def _block_summing_to(parts: dict, total_index: int, total: float) -> list[str] | None:
    """The rows right next to a total row that add up to it, when the rows read span more than it covers: AMD's
    segment table lists Data Center and Embedded beside the Client and Gaming rows its "Client and Gaming" total sums."""
    ordered = sorted(parts, key=lambda key: parts[key][2])
    above = [key for key in ordered if parts[key][2] < total_index][::-1]
    below = [key for key in ordered if parts[key][2] > total_index]
    for side in (above, below):
        running, block = 0.0, []
        for key in side:
            running += parts[key][1]
            block.append(key)
            if len(block) >= 2 and abs(running - total) <= abs(total) * 0.001:
                return block
    return None


def _total_row_by_sum(table: Table, locator: TableLocator, parts: dict, read_row) -> int | None:
    """The row just below (or above) the rows read whose figure is their sum, when the reader named no total row:
    TSMC's "Net revenue" line under its geography table. Found by the numbers, never by the label alone."""
    parts_sum = sum(value for _label, value, _index in parts.values())
    for index in (locator.last_row + 1, locator.last_row + 2, locator.first_row - 1):
        if 0 <= index < len(table.rows):
            try:
                row = read_row(index)
            except LocateError:
                continue
            if row and parts_sum and abs(row[1] - parts_sum) <= abs(parts_sum) * 0.001:
                return index
    return None


def _currency(text: str | None, kpi: KpiSpec, default_currency: str | None, context: str = "") -> str | None:
    """A value's currency, most specific evidence first: a marker on the value itself (NT$, US$, €), then what its
    table or passage declares, then the company's reporting currency. A bare "$" is only a dollar sign: in TSMC's
    reports it means New Taiwan dollars, so it defers to the declaration and the reporting currency."""
    if kpi.unit not in ("currency", "ratio"):
        return None
    for marker, code in _CURRENCY_MARKERS:
        if marker != "$" and text and marker in text:
            return code
    declared = declared_currency(context)
    if declared:
        return declared
    if text and "$" in text:
        return default_currency or "USD"
    return default_currency if kpi.unit == "currency" else None


def _column_currency(table: Table, row: int, col: int, text: str | None) -> str | None:
    """The one currency a row label or a column's own header names, when its table gives each figure in two: Auna's
    "2Q'25 (USD)" beside "2Q'25" under "(Figures in millions of Soles and millions of US Dollars)". A marker on the value
    comes first."""
    if text and any(marker != "$" and marker in text for marker, _ in _CURRENCY_MARKERS):
        return None
    # A row label's own currency, Axia's "Average ACL Price (R$/MWh)".
    label = table.rows[row][0] if 0 <= row < len(table.rows) and table.rows[row] else ""
    if marked := next((code for marker, code in _CURRENCY_MARKERS if marker != "$" and marker in label), None):
        return marked
    cells = " ".join(_heading(header, col) for header in table.rows[:max(row, 1)][:6] if col < len(header))
    found = {code for pattern, code in _DECLARED_CURRENCIES if pattern.search(cells)}
    return found.pop() if len(found) == 1 else None


def _heading(row: list[str], col: int) -> str:
    # A header spanning several columns sits on the first of them ("Quarter Ended December 31," over 2022 and 2023):
    # an empty header cell takes the nearest one to its left, never the row-label column.
    for index in range(min(col, len(row) - 1), 0, -1):
        if row[index].strip():
            return row[index]
    return ""


_MONTHS = {name: number for number, name in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                                          "nov", "dec"), start=1)}
_MONTH_NAME = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|" \
              r"jul|aug|sept?|oct|nov|dec)\.?"
_MONTH_SPAN = re.compile(rf"\b{_MONTH_NAME}\s*(?:to|through|thru|-|–|—)\s*{_MONTH_NAME}(?![\w])")


def column_header(table: Table, first_data_row: int, col: int) -> str:
    """The words heading a column ("Three Months Ended @ #") with dates and numbers blanked, so a quarter's column and a
    six-month or prior-year column never pass for one another when a reading is replayed."""
    cells = [cell for row in table.rows[:max(first_data_row, 1)][:6] if col < len(row) and re.search(r"[A-Za-z]", cell := _heading(row, col))]
    text = clean(" ".join(cells)).lower()
    # A span of months names its length: Philips' "January to June" is a half-year, "April to June" a quarter.
    text = _MONTH_SPAN.sub(lambda m: f"<{(_MONTHS[m.group(2)[:3]] - _MONTHS[m.group(1)[:3]]) % 12 + 1} months>", text)
    text = re.sub(r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
                  r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b", "@", text)
    # A duration names the column ("3 months", "six months", "26 weeks"), so its number is kept; dates and years blank.
    text = re.sub(r"(?<!<)\b(\d{1,2})(\s*-?\s*)(months?|weeks?|quarters?)\b(?!>)", r"<\1 \3>", text)
    return re.sub(r"(?<![<\d])[\d.,]+(?![\d]* (?:months?|weeks?|quarters?)>)", "#", text)[:120]


def _locator_record(document: Document, locator: Locator) -> dict:
    """What the next quarter's prompt is told about where this KPI was found."""
    if locator.table is not None:
        table = document.tables[locator.table]
        row = table.rows[locator.row] if locator.row < len(table.rows) else []
        cells = [parse_number(cell) for cell in row]
        return {"table": locator.table, "row": locator.row, "col": locator.col,
                "row_label": _row_label(row, locator.col)[:80], "context": table.context[-80:], "scale": locator.scale,
                "header": column_header(table, locator.row, locator.col),
                # The row's own numbers as printed, the read cell first: a repeat the row itself shows is no wrong column.
                "row_numbers": [cells[locator.col].value if locator.col < len(cells) and cells[locator.col] else None,
                                *[c.value for i, c in enumerate(cells) if c is not None and i != locator.col][:8]]}
    return {"block": locator.block, "quote": clean(locator.quote or "")[:160], "value_text": locator.value_text}


def _row_label(row: list[str], col: int) -> str:
    """A row's label: the first cell with words to the left of the value (not a "$ -" filler in another column), without
    a footnote marker run into it ("Banking & Wealth Management14", "devices(1)")."""
    label = next((cell for cell in row[:col] if re.search(r"[A-Za-z]", cell)),
                 next((cell for cell in row if re.search(r"[A-Za-z]", cell) and not re.search(r"\d", cell)), ""))
    return _FOOTNOTE.sub("", label).strip()


_FOOTNOTE = re.compile(r"(?:(?<=[a-z]{3})\d{1,2}|\s*\(\d{1,2}\)|\s*\([a-z]\))$")


def locator_hint(locator: dict | None) -> str | None:
    if not locator:
        return None
    if "quote" in locator:
        return f"text like {locator['quote']!r}"
    return f"table row {locator.get('row_label')!r}"


def describe(document: Document, item: ReadValue) -> str:
    """What a reading points at, for a reviewer: the value with its table row and column headers, or its quote."""
    locator = item.locator
    if "table" in locator:
        table = document.tables[locator["table"]]
        column = locator["col"]
        headers = [row[column] for row in table.rows[:locator["row"]][:5] if column < len(row) and row[column]]
        cell = table.cell(locator["row"], column) or ""
        return (f"{cell!r} in {locator['table']} row {locator.get('row_label')!r}, column {' / '.join(headers)!r}"
                f" (read as {item.value:g})")
    return f"{locator.get('value_text')!r} in {locator.get('block')}: {locator.get('quote')!r} (read as {item.value:g})"


_MONTHS = {name: index for index, names in enumerate(
    [("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"), ("july", "jul"),
     ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"), ("december", "dec")], 1)
    for name in names}
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/((?:19|20)\d{2})\b")
_FULL_DATE = re.compile(r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{1,2}),?\s+((?:19|20)\d{2})\b")
_YEAR = re.compile(r"(?<![\d$.,])(?:fy\s?)?'?((?:19|20)\d{2})(?![\d%]|[,.]\d)|\b[1-4]q'?(\d{2})\b|\bq[1-4]\s?'?(\d{2}|(?:19|20)\d{2})\b")


def column_dates(table: Table, first_data_row: int, col: int) -> tuple[list[date], list[int]]:
    """The dates and years a column's own header cells name, read from the header rows above the first data row
    (the words column_header keeps, here with their dates): "Three Months Ended September 30," over "2026" gives
    2026-09-30. A two-digit year ("3Q25", "Q3'25") counts as 20xx."""
    cells = [_heading(row, col) for row in table.rows[:max(first_data_row, 1)][:6] if col < len(row)]
    text = clean(" ".join(cell for cell in cells if cell)).lower()
    dates = []
    for month, day, year in _FULL_DATE.findall(text):
        try:
            dates.append(date(int(year), _MONTHS[month], int(day)))
        except ValueError:
            continue
    for month, day, year in _NUMERIC_DATE.findall(text):  # PepsiCo's "12 Weeks Ended 6/14/2025"
        try:
            dates.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    years = sorted({int(full) if full else 2000 + int(short or quarter[-2:]) for full, short, quarter in _YEAR.findall(text)
                    if (full or short or quarter)})
    return dates, years


def header_contradicts(table: Table, row: int, col: int, expected: date, *also: date) -> str | None:
    """Why a value's column is not the reported quarter's, from the dates its header names; None when the header
    agrees or names none. A full date must be within ten days of the quarter's end. A header naming only years
    contradicts only when every year is before the quarter's own (the year-ago column): fiscal years named ahead of
    the calendar (Apple's Q1 FY2027 ends in December 2026) never do."""
    dates, years = column_dates(table, row, col)
    if dates:
        if any(abs((day - end).days) <= 10 for day in dates for end in (expected, *also)):
            return None
        return f"column headed {max(dates).isoformat()}, not the quarter ended {expected.isoformat()}"
    if years and max(years) < (expected - timedelta(days=10)).year:
        return f"column headed {max(years)}, not the quarter ended {expected.isoformat()}"
    return None


def _label_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _FOOTNOTE.sub("", clean(text or "")).lower())


def labels_agree(named: str | None, actual: str) -> bool:
    """Whether the row label a reader names is the row's own: the same words once case, punctuation and footnote marks
    are set aside, one label inside the other ("Wafer" in "Wafer revenue"), or at least half the words shared. A
    reader that names no label, or a row without one, has nothing to check."""
    named_words, actual_words = _label_words(named or ""), _label_words(actual)
    if not named_words or not actual_words:
        return True
    a, b = "".join(named_words), "".join(actual_words)
    if a in b or b in a:
        return True
    shared = set(named_words) & set(actual_words)
    return len(shared) >= 0.5 * min(len(set(named_words)), len(set(actual_words)))


# A metric whose name gives a place ("Japan Policyholder Account Balances") read from a row that names no such place,
# under no heading or caption that does (Prudential's "International Businesses" total): held for review.
PLACE_NOT_NAMED = "the row and its headings name no"
_PLACE = re.compile(r"\b(Japan|China|U\.?S\.?|United States|North America|Latin America|Americas|Europe|EMEA|APAC|"
                    r"Asia Pacific|Asia|Canada|Mexico|Brazil|Germany|U\.?K\.?|United Kingdom|India|Korea|Taiwan|France|"
                    r"Australia|Africa|Middle East|Peru|Colombia|Chile|Türkiye|Turkey|Spain|Italy)\b", re.I)
# A place a name leaves out ("Excluding Intra-Europe") is not the figure's own.
_NOT_THE_PLACE = re.compile(r"(?:excluding|excl\.?|ex|intra|non|outside|other than)[- ]\s*$", re.I)


def _places(text: str, own: bool = False) -> set[str]:
    return {match.group(0).replace(".", "").upper() for match in _PLACE.finditer(text)
            if not (own and _NOT_THE_PLACE.search(text[:match.start()][-16:]))}


def _place_not_named(label: str, table: Table, row: int, col: int) -> str | None:
    """The place a metric's name gives that its row, its section headings, its column and its table's caption all
    leave unnamed, if any."""
    places = _places(label, own=True)
    if not places:
        return None
    around = " ".join([" ".join(table.rows[row]) if row < len(table.rows) else "", _section_heading(table, row),
                       " ".join(_heading(header, col) for header in table.rows[:row][:6] if col < len(header)),
                       table.context[-200:]])
    named = _places(around)
    missing = places - named
    return ", ".join(sorted(missing)) if missing else None


# A figure whose scale only the AI gives: held for review, never served on the AI's word.
SCALE_NOT_STATED = "scale not stated in the document"
# A column's own scale: Mastercard's "(Bil.)" and "(Mil.)", Axia's "(R$ mn)". A single letter counts only beside a
# currency sign ("($B)"): a bare "(b)" is a footnote mark, as in Axia's "Regulatory Revenue (c) = (a) x (b)".
_COLUMN_SCALE = re.compile(r"\(\s*(?:[A-Z]{1,3})?\$?\s*(?P<word>bil|bn|billions?|mil|mm|mn|millions?|thous|thousands?|000s?)\.?\s*\)"
                           r"|\(\s*(?:[A-Z]{1,3})?\$\s*(?P<letter>[bmk])\.?\s*\)"
                           r"|(?<![\w.])(?:€|\$|£|¥)\s*(?P<bare>bn|mn|mm|m|k)\b", re.I)  # Vodafone's "€m" column heading
_SCALE_OF = (("bil", 1e9), ("bn", 1e9), ("b", 1e9), ("mil", 1e6), ("mm", 1e6), ("mn", 1e6), ("m", 1e6), ("thous", 1e3), ("k", 1e3),
             ("000", 1e3))


def _scale_word(word: str | None) -> float | None:
    if not word:
        return None
    word = word.lower()
    return next((scale for prefix, scale in _SCALE_OF if word.startswith(prefix)), None)


def _column_scale(table: Table, row: int, col: int) -> float | None:
    """The scale a column's own header states: Mastercard's "GDV (Bil.)" and "Purchase Trans. (Mil.)"."""
    cells = [_heading(header, col) for header in table.rows[:max(row, 1)][:6] if col < len(header)]
    found = {_scale_word(match.group("word") or match.group("letter") or match.group("bare")) for cell in cells for match in _COLUMN_SCALE.finditer(cell)} - {None}
    return found.pop() if len(found) == 1 else None


def _scale_quoted(document: Document, part, scale_text: str | None, scale: float) -> bool:
    """Whether the words a reader quotes for a scale are in the table (its caption, header or rows) or the passages
    just before it, and state that scale."""
    words = clean(scale_text or "")
    if not words:
        return False
    stated = {_SCALE_WORDS_OF[match.group("word").lower().rstrip("s")] for match in _ANY_SCALE.finditer(words)}
    stated |= {_scale_word(match.group("word") or match.group("letter") or match.group("bare")) for match in _COLUMN_SCALE.finditer(words)} - {None}
    if stated != {scale}:
        return False
    if isinstance(part, Table):
        nearby = [part.context, *(" ".join(row) for row in part.rows)]
    else:
        nearby = [part.text]
    key = getattr(part, "id", None)
    if key in document.order:
        at = document.order.index(key)
        nearby += [document.blocks[other].text for other in document.order[max(0, at - 4):at] if other in document.blocks]
    flat = lambda text: re.sub(r"\s+", " ", clean(text)).lower()
    return any(flat(words) in flat(text) for text in nearby)


_ANY_SCALE = re.compile(r"\b(?P<word>thousands?|millions?|billions?)\b", re.I)
_SCALE_WORDS_OF = {"thousand": 1e3, "million": 1e6, "billion": 1e9}


def _ai_scale_notes(document: Document, part, scale_text: str | None, scale: float, value: float) -> list[str]:
    """A scale code did not find: one the reader quotes from the document stands, anything else is held for review.
    Scale 1 claims no multiplier (a price per unit, a figure written out in full) and stands as read."""
    if scale == 1:
        return []
    if _scale_quoted(document, part, scale_text, scale):
        return [f"scale quoted: {clean(scale_text or '')[:40]!r}"]
    return [SCALE_NOT_STATED]


def _section_heading(table: Table, row: int) -> str:
    """The heading rows just above a row inside its table (a label with no figures): PepsiCo's "Impact on % Change of:"
    over its organic volume change, whose cells print no % sign."""
    headings = []
    for index in range(row - 1, max(-1, row - 4), -1):
        cells = table.rows[index] if index < len(table.rows) else []
        if not cells or any(parse_number(cell) for cell in cells[1:]):
            break
        headings.append(" ".join(cells))
    return " ".join(headings)
