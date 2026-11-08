"""The three statements from a filing's XBRL (10-Q, 10-K, 20-F, 40-F): every column the filing presents, its own
period and the earlier ones it shows again, each line in the filing's order with its own label and indentation.

Read from the statement's presentation as filed (edgartools' raw rows), not edgartools' table view: the raw rows keep
each figure as reported (unsigned, with its rounding), each period's start date, and which line is the opening and
which the closing cash. Labels are the filer's own, from its label file in the role its presentation asks for; the
table view can put its own standard names in their place on older filings (Apple's 2009 10-K showed short-term
marketable securities as "Cash and Cash Equivalents").
"""
from __future__ import annotations

import math
import re
from datetime import date

from .checks import Column, Figure, checks_for
from .model import Reading
from .periods import OWN_SLACK, Period, label_period, opening_instant, parse_period, presented_periods

_STATEMENTS = (("income", ("income_statement",)), ("balance", ("balance_sheet",)),
               ("cash_flow", ("cash_flow_statement", "cashflow_statement")))
STANDARD_LABEL = "http://www.xbrl.org/2003/role/label"
# Taxonomy scaffolding the filer did not title: "Statement [Table]", "Statement [Line Items]", "… [Abstract]".
_STRUCTURAL_LABEL = re.compile(r"\[(abstract|line items|table|axis|domain|member)\]\s*$", re.I)
_STRUCTURAL_CONCEPT = re.compile(r"(StatementTable|StatementLineItems|Axis|Domain|Member)$")


def _number(raw) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) or math.isinf(value) else value


def _decimals(raw) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None  # "INF" or not given: exact as reported


def clean_label(raw: str) -> str:
    """The filing's label, in sentence case where the filer wrote it in capitals (TSMC's "NET REVENUE")."""
    label = re.sub(r"\s+", " ", raw).strip()
    letters = [char for char in label if char.isalpha()]
    if len(letters) > 3 and all(char.isupper() for char in letters):
        label = label[0] + label[1:].lower()
    return label[:300]


class ReportingCurrency:
    """A filer may tag a line twice for one period: in its own currency and as a convenience translation (TSMC's 20-F
    gives the latest year in US dollars as well). Each line takes the figure in the currency the filing reports in."""

    def __init__(self, xbrl):
        self.currency: str | None = None
        self.facts: dict[tuple[str, str], list[tuple[str, float, str]]] = {}
        try:
            facts = xbrl.facts.to_dataframe()
        except Exception:  # noqa: BLE001 - without facts the statement's own figures stand
            return
        if facts is None or facts.empty or "currency" not in facts:
            return
        if "is_dimensioned" in facts:
            facts = facts[facts["is_dimensioned"] != True]  # noqa: E712 - pandas mask
        monetary = facts[facts["currency"].notna() & ~facts["unit_ref"].astype(str).str.contains("per", case=False)]
        if monetary.empty:
            return
        self.currency = str(monetary["currency"].value_counts().idxmax())
        for row in monetary.to_dict("records"):
            when = row.get("period_end") if isinstance(row.get("period_end"), str) else row.get("period_instant")
            value = _number(row.get("numeric_value"))
            if isinstance(when, str) and value is not None:
                self.facts.setdefault((str(row["concept"]), when[:10]), []).append(
                    (str(row["currency"]), value, str(row.get("period_key"))))

    def value(self, concept: str, when: str, value: float) -> float:
        candidates = self.facts.get((concept.replace("_", ":", 1), when))
        if not candidates or self.currency is None:
            return value
        shown = next((fact for fact in candidates if fact[1] == value), None)
        if shown is None or shown[0] == self.currency:
            return value
        own = next((fact for fact in candidates if fact[0] == self.currency and fact[2] == shown[2]), None)
        return own[1] if own else value


def _statement(xbrl, methods: tuple[str, ...]):
    for method in methods:
        reader = getattr(xbrl.statements, method, None)
        if reader is None:
            continue
        try:
            return reader()
        except Exception:  # noqa: BLE001 - a filing without this statement just has none to show
            return None
    return None


def _labels(xbrl, concept: str) -> dict[str, str]:
    catalog = getattr(xbrl, "element_catalog", None) or {}
    element = catalog.get(concept) or catalog.get(concept.replace("_", ":", 1))
    return dict(getattr(element, "labels", None) or {})


def _arcs(xbrl, role: str | None) -> dict[str, list[tuple[str, float]]]:
    tree = (getattr(xbrl, "calculation_trees", None) or {}).get(role) if role else None
    arcs: dict[str, list[tuple[str, float]]] = {}
    for arc in getattr(tree, "all_arcs", None) or []:
        arcs.setdefault(arc.parent_id, []).append((arc.child_id, float(arc.weight)))
    return arcs


def _rows(statement) -> list[dict]:
    rows = []
    for row in statement.get_raw_data() or []:
        if row.get("is_dimension") or row.get("dimension"):
            continue  # a breakdown some filers tag inside a statement, not one of its own lines
        concept, label = str(row.get("concept") or ""), str(row.get("label") or "")
        if row.get("is_abstract") and (_STRUCTURAL_LABEL.search(label) or _STRUCTURAL_CONCEPT.search(concept)):
            continue
        if _STRUCTURAL_CONCEPT.search(concept):
            continue
        rows.append(row)
    return rows


def _is_instant_line(row: dict) -> bool:
    keys = list((row.get("values") or {}).keys())
    return bool(keys) and all(key.startswith("instant_") for key in keys)


def _annotated(xbrl, rows: list[dict], flows: bool) -> list[dict]:
    """Each line with the filer's labels and, in a flow statement, whether a balance it shows is the opening or the
    closing one: by the role the presentation gives it ("beginning of period"), else, for a balance shown twice with
    no role, the first is the opening one."""
    seen: dict[str, int] = {}
    counts: dict[str, int] = {}
    for row in rows:
        if flows and _is_instant_line(row):
            counts[str(row["concept"])] = counts.get(str(row["concept"]), 0) + 1
    annotated = []
    for row in rows:
        concept = str(row["concept"])
        balance = None
        if flows and _is_instant_line(row):
            role = str(row.get("preferred_label") or "")
            occurrence = seen.get(concept, 0)
            seen[concept] = occurrence + 1
            if "periodStart" in role:
                balance = "opening"
            elif "periodEnd" in role:
                balance = "closing"
            else:
                balance = "opening" if counts[concept] > 1 and occurrence == 0 else "closing"
        annotated.append({**row, "_labels": _labels(xbrl, concept), "_balance": balance})
    return annotated


def _key_for(row: dict, period: Period, instants: dict[date, str]) -> str | None:
    """Which of a line's figures belongs in this column: the period's own, or for a balance shown in a flow statement
    (opening and closing cash), the instant the period opens or closes on."""
    if period.start is None or not row.get("_balance"):
        return period.key
    if row["_balance"] == "opening":
        return opening_instant(period, instants)
    return f"instant_{period.end}"


def read_filing(xbrl, *, accession: str, form: str, filed: date, fiscal_year_end: str,
                year_offset: int) -> tuple[list[Reading], list[str]]:
    """Every statement column of one filing, checked; and notes on what it could not read."""
    entity = xbrl.entity_info or {}
    try:
        document_end = date.fromisoformat(str(entity.get("document_period_end_date"))[:10])
    except ValueError:
        return [], ["no document period end"]
    currency = ReportingCurrency(xbrl)
    readings: list[Reading] = []
    notes: list[str] = []
    for name, methods in _STATEMENTS:
        statement = _statement(xbrl, methods)
        if statement is None:
            notes.append(f"{name}: not found")
            continue
        try:
            rows = _annotated(xbrl, _rows(statement), flows=name != "balance")
        except Exception as error:  # noqa: BLE001
            notes.append(f"{name}: unreadable ({type(error).__name__})")
            continue
        arcs = _arcs(xbrl, getattr(statement, "role_or_type", None))
        periods = presented_periods([row for row in rows if not row.get("is_abstract")], instants=name == "balance")
        instants = {period.end: period.key for row in rows for key in (row.get("values") or {})
                    if (period := parse_period(key)) is not None and period.start is None}
        for period in periods:
            labelled = label_period(period, fiscal_year_end, year_offset)
            if labelled is None:
                notes.append(f"{name}: column {period.key} off the calendar")
                continue
            reading = _read_column(name, rows, period, instants, arcs, currency, labelled)
            if reading is None:
                continue
            reading.accession, reading.form, reading.filed = accession, form, filed
            reading.own = abs(period.end - document_end) <= OWN_SLACK
            readings.append(reading)
    return readings, notes


def _read_column(name: str, rows: list[dict], period: Period, instants: dict[date, str], arcs, currency,
                 labelled: tuple[str, str, str]) -> Reading | None:
    fiscal_year, fiscal_period, duration = labelled
    lines: list[dict] = []
    column = Column()
    levels = [int(level) for level in (_number(row.get("level")) for row in rows) if level is not None]
    base = min(levels) if levels else 0
    for row in rows:
        concept = str(row["concept"])
        heading = bool(row.get("is_abstract"))
        value = None
        if not heading:
            key = _key_for(row, period, instants)
            raw = _number((row.get("values") or {}).get(key)) if key else None
            if raw is None:
                column.blank.add(concept)
                continue  # a line this period does not report
            when = parse_period(key).end.isoformat()
            raw = currency.value(concept, when, raw)
            figure = Figure(raw, _decimals((row.get("decimals") or {}).get(key)))
            sign = _number((row.get("preferred_signs") or {}).get(key)) or 1.0
            value = raw * sign
            if period.start is not None and row.get("_balance") == "opening":
                column.opening = column.opening or figure
            elif period.start is not None and row.get("_balance") == "closing":
                column.closing = figure  # the last balance a flow statement shows is its closing one
            else:
                column.figures.setdefault(concept, figure)
        labels = _labels_for(row)
        level = _number(row.get("level"))
        lines.append({
            "line": len(lines), "concept": concept[:200], "label": clean_label(labels),
            "level": max(0, min(20, int(level) - base)) if level is not None else 0,
            "is_heading": heading, "value": value,
        })
    lines = [line for index, line in enumerate(lines) if not line["is_heading"] or _has_lines_below(lines, index)]
    if not any(not line["is_heading"] for line in lines):
        return None
    for index, line in enumerate(lines):
        line["line"] = index
    return Reading(
        statement=name, duration=duration, fiscal_year=fiscal_year, fiscal_period=fiscal_period,
        period_end=period.end, period_start=period.start, currency=currency.currency,
        accession="", form="", filed=date.min, own=False, source="xbrl",
        lines=lines, checks=checks_for(name, column, arcs),
    )


def _labels_for(row: dict) -> str:
    """The filer's own label in the role its presentation asks for (a total's, a period start's), else its standard
    one, else what edgartools shows."""
    labels = row.get("_labels") or {}
    role = row.get("preferred_label")
    return str((labels.get(role) if role else None) or labels.get(STANDARD_LABEL) or row.get("label") or row["concept"])


def _has_lines_below(lines: list[dict], index: int) -> bool:
    """Whether a heading has a line before the next heading at its own level or above."""
    level = lines[index]["level"]
    for later in lines[index + 1:]:
        if not later["is_heading"]:
            return True
        if later["level"] <= level:
            return False
    return False
