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


def _facts(xbrl):
    try:
        facts = xbrl.facts.to_dataframe()
    except Exception:  # noqa: BLE001 - without facts the statement's own figures stand
        return None
    return None if facts is None or getattr(facts, "empty", True) else facts


def consolidated_figures(facts) -> set[tuple[str, str]]:
    """(concept, period) of every figure tagged for the company as a whole. A line whose figure for a period exists only
    for a part of it (JPM's memo of consolidated VIEs' assets and liabilities, "included in the line items above") is
    not a line of that period's statement: counted as one, it is counted twice."""
    if facts is None or "is_dimensioned" not in facts or "period_key" not in facts:
        return set()
    plain = facts[facts["is_dimensioned"] != True]  # noqa: E712 - pandas mask
    return {(str(concept).replace(":", "_", 1), str(key)) for concept, key in zip(plain["concept"], plain["period_key"])}


class ReportingCurrency:
    """A filer may tag a line twice for one period: in its own currency and as a convenience translation (TSMC's 20-F
    gives the latest year in US dollars as well). Each line takes the figure in the currency the filing reports in."""

    def __init__(self, facts):
        self.currency: str | None = None
        self.facts: dict[tuple[str, str], list[tuple[str, float, str]]] = {}
        if facts is None or "currency" not in facts:
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
    """Each line with the filer's labels and, in a flow statement, which balance a balance line is: the opening one (by
    the role its presentation gives it, "beginning of period", else the first of a balance shown twice), the closing
    one (that same line shown again at the end), or another (a reconciliation after the closing balance: Coca-Cola's
    "less restricted cash" down to the balance sheet's cash). Each other balance is shown at the period's end."""
    balances = [index for index, row in enumerate(rows) if flows and _is_instant_line(row)]
    concept_of = {index: str(rows[index]["concept"]) for index in balances}
    starts = [index for index in balances if "periodStart" in str(rows[index].get("preferred_label") or "")]
    if not starts:
        starts = [next((index for index in balances
                        if sum(1 for other in balances if concept_of[other] == concept_of[index]) > 1), None)]
        starts = [index for index in starts if index is not None]
    # The main opening balance is the one shown again at the end; others open beside it (TSMC's 2015 cash held for
    # sale) and count towards the opening cash.
    shown_again = [index for index in starts
                   if any(other > index and concept_of[other] == concept_of[index] for other in balances)]
    main = shown_again[0] if shown_again else (starts[0] if starts else None)
    closing = None
    if main is not None:
        closing = next((index for index in reversed(balances)
                        if index > main and concept_of[index] == concept_of[main]), None)
    if closing is None:
        closing = next((index for index in reversed(balances)
                        if "periodEnd" in str(rows[index].get("preferred_label") or "")), balances[-1] if balances else None)
    opening = set(starts) - {closing}
    annotated = []
    for index, row in enumerate(rows):
        balance = None
        if index in concept_of:
            balance = "opening" if index in opening else "closing" if index == closing else "other"
        annotated.append({**row, "_labels": _labels(xbrl, str(row["concept"])), "_balance": balance, "_index": index})
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
    facts = _facts(xbrl)
    currency = ReportingCurrency(facts)
    consolidated = consolidated_figures(facts)
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
            reading = _read_column(name, rows, period, instants, arcs, currency, consolidated, labelled)
            if reading is None:
                continue
            reading.accession, reading.form, reading.filed = accession, form, filed
            reading.own = abs(period.end - document_end) <= OWN_SLACK
            readings.append(reading)
    return readings, notes


def _read_column(name: str, rows: list[dict], period: Period, instants: dict[date, str], arcs, currency,
                 consolidated: set[tuple[str, str]], labelled: tuple[str, str, str]) -> Reading | None:
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
            if consolidated and (concept, key) not in consolidated and not (
                    period.start is not None and row.get("_balance") in ("opening", "closing")):
                # A figure for part of the company only: a memo of consolidated VIEs, a breakdown of cash by kind. The
                # opening and closing balances of a flow statement are kept: a filer can tag the company's own balance
                # under a stray dimension (Prudential's 2026 cash), and the cash roll-forward checks them anyway.
                continue
            when = parse_period(key).end.isoformat()
            raw = currency.value(concept, when, raw)
            figure = Figure(raw, _decimals((row.get("decimals") or {}).get(key)))
            sign = _number((row.get("preferred_signs") or {}).get(key)) or 1.0
            value = raw * sign
            if period.start is not None and row.get("_balance") == "opening":
                column.opening = figure if column.opening is None else Figure(
                    column.opening.value + figure.value, _coarser(column.opening.decimals, figure.decimals))
            elif period.start is not None and row.get("_balance") == "closing":
                column.closing = figure
            elif not (period.start is not None and row.get("_balance") == "other"):
                if concept not in column.figures:
                    column.figures[concept] = figure
                    column.presented.append((concept, str(row.get("parent") or ""), sign))
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


def _coarser(left: int | None, right: int | None) -> int | None:
    known = [value for value in (left, right) if value is not None]
    return min(known) if known else None


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
