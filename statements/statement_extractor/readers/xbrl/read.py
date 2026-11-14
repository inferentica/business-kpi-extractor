"""The three statements from a filing's XBRL (10-Q, 10-K, 20-F, 40-F): every column the filing presents, its own
period and the earlier ones it shows again, each line in the filing's order with its own label and indentation.

Read from the statement's presentation as filed (edgartools' raw rows), not edgartools' table view: the raw rows keep
each figure as reported (unsigned, with its rounding), each period's start date, and which line is the opening and
which the closing cash. Labels are the filer's own, from its label file in the role its presentation asks for; the
table view can put its own standard names in their place on older filings (Apple's 2009 10-K showed short-term
marketable securities as "Cash and Cash Equivalents").
"""
from __future__ import annotations

import re
from datetime import date

from ...checks.reported import Column, Figure, checks_for
from ...domain.basis import taxonomy_basis
from ...domain.model import Reading
from ...domain.periods import (OWN_SLACK, Period, label_period, opening_instant, parse_period, presented_periods,
                               short_first_year)
from .facts import ReportingCurrency, consolidated_figures, decimals, load_facts, number
from .rows import (STATEMENT_METHODS, annotated, clean_label, is_instant_line, key_for, label_for, presented_rows,
                   statement_of)


def _arcs(xbrl, role: str | None) -> dict[str, list[tuple[str, float]]]:
    tree = (getattr(xbrl, "calculation_trees", None) or {}).get(role) if role else None
    arcs: dict[str, list[tuple[str, float]]] = {}
    for arc in getattr(tree, "all_arcs", None) or []:
        arcs.setdefault(arc.parent_id, []).append((arc.child_id, float(arc.weight)))
    return arcs


def _shown(xbrl, role: str | None) -> set[str]:
    """The axes and members a statement's own presentation shows."""
    tree = (getattr(xbrl, "presentation_trees", None) or {}).get(role) if role else None
    nodes = getattr(tree, "all_nodes", None) or {}
    return {str(node) for node in nodes if str(node).endswith(("Axis", "Member"))}


def read_filing(xbrl, *, accession: str, form: str, filed: date, fiscal_year_end: str,
                year_offset: int) -> tuple[list[Reading], list[str]]:
    """Every statement column of one filing, checked; and notes on what it could not read."""
    entity = xbrl.entity_info or {}
    try:
        document_end = date.fromisoformat(str(entity.get("document_period_end_date"))[:10])
    except ValueError:
        return [], ["no document period end"]
    facts = load_facts(xbrl)
    currency = ReportingCurrency(facts)
    consolidated = consolidated_figures(facts)
    readings: list[Reading] = []
    notes: list[str] = []
    for name, methods in STATEMENT_METHODS:
        statement = statement_of(xbrl, methods)
        if statement is None:
            notes.append(f"{name}: not found")
            continue
        try:
            shown = _shown(xbrl, getattr(statement, "role_or_type", None))
            rows = annotated(xbrl, presented_rows(statement, shown), flows=name != "balance")
        except Exception as error:  # noqa: BLE001
            notes.append(f"{name}: unreadable ({type(error).__name__})")
            continue
        arcs = _arcs(xbrl, getattr(statement, "role_or_type", None))
        periods = presented_periods([row for row in rows if not row.get("is_abstract")], instants=name == "balance")
        instants = {period.end: period.key for row in rows for key in (row.get("values") or {})
                    if (period := parse_period(key)) is not None and period.start is None}
        for period in periods:
            labelled = label_period(period, fiscal_year_end, year_offset) or \
                short_first_year(period, periods, fiscal_year_end, year_offset, form)
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
                 consolidated: dict[tuple[str, str], dict], labelled: tuple[str, str, str]) -> Reading | None:
    fiscal_year, fiscal_period, duration = labelled
    lines: list[dict] = []
    column = Column()
    levels = [int(level) for level in (number(row.get("level")) for row in rows) if level is not None]
    base = min(levels) if levels else 0
    for row in rows:
        concept = str(row["concept"])
        heading = bool(row.get("is_abstract"))
        member = row.get("_member")
        value = None
        if member and not heading:
            key = period.key
            if period.start is not None and is_instant_line(row):  # a balance by segment in a flow statement
                opens = row.get("_member_balance") == "opening"
                key = opening_instant(period, instants) if opens else f"instant_{period.end}"
            raw = number((row.get("values") or {}).get(key)) if key else None
            if raw is None:
                continue  # a breakdown this period does not print
            raw = currency.value(concept, parse_period(key).end.isoformat(), raw)
            value = raw * (number((row.get("preferred_signs") or {}).get(key)) or 1.0)
        elif not heading:
            key = key_for(row, period, instants)
            raw = number((row.get("values") or {}).get(key)) if key else None
            if raw is None:
                column.blank.add(concept)
                continue  # a line this period does not report
            if consolidated and (concept, key) not in consolidated and not (
                    period.start is not None and row.get("_balance") in ("opening", "closing")):
                # A figure for part of the company only: a memo of consolidated VIEs, a breakdown of cash by kind. The
                # opening and closing balances of a flow statement are kept: a filer can tag the company's own balance
                # under a stray dimension (Prudential's 2026 cash), and the cash roll-forward checks them anyway.
                continue
            places = (row.get("decimals") or {}).get(key)
            own = consolidated.get((concept, key)) or {}
            if len(own) == 1 and raw not in own and -raw not in own:
                (raw, places), = own.items()  # the company's own figure, not a part's (facts.consolidated_figures)
            when = parse_period(key).end.isoformat()
            raw = currency.value(concept, when, raw)
            figure = Figure(raw, decimals(places))
            sign = number((row.get("preferred_signs") or {}).get(key)) or 1.0
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
        labels = str(row.get("label") or member) if member else label_for(row)  # a breakdown's label is its member
        level = number(row.get("level"))
        lines.append({
            "line": len(lines), "concept": concept[:200], "label": clean_label(labels),
            "level": max(0, min(20, int(level) - base)) if level is not None else 0,
            "is_heading": heading, "value": value, **({"member": member} if member else {}),
        })
    if name == "balance":
        lines = _through_grand_total(lines)
    lines = [line for index, line in enumerate(lines) if not line["is_heading"] or _has_lines_below(lines, index)]
    if not any(not line["is_heading"] for line in lines):
        return None
    for index, line in enumerate(lines):
        line["line"] = index
    return Reading(
        statement=name, duration=duration, fiscal_year=fiscal_year, fiscal_period=fiscal_period,
        period_end=period.end, period_start=period.start, currency=currency.currency,
        accession="", form="", filed=date.min, own=False, source="xbrl",
        basis=taxonomy_basis([line["concept"] for line in lines]), lines=lines, checks=checks_for(name, column, arcs),
    )


_GRAND_TOTAL = re.compile(r"^(us-gaap_LiabilitiesAndStockholdersEquity|ifrs-full_EquityAndLiabilities)$")


def _through_grand_total(lines: list[dict]) -> list[dict]:
    """A balance sheet's lines up to its total liabilities and equity. Lines after it are a memo, not lines of the
    statement (JPM's FY2010 "limited program-wide credit enhancement", tagged without a dimension and printed
    nowhere on the balance sheet); a figure counted there would be counted twice."""
    last = max((index for index, line in enumerate(lines) if _GRAND_TOTAL.match(line["concept"])), default=None)
    return lines if last is None else lines[:last + 1]


def _coarser(left: int | None, right: int | None) -> int | None:
    known = [value for value in (left, right) if value is not None]
    return min(known) if known else None


def _has_lines_below(lines: list[dict], index: int) -> bool:
    """Whether a heading has a line before the next heading at its own level or above."""
    level = lines[index]["level"]
    for later in lines[index + 1:]:
        if not later["is_heading"]:
            return True
        if later["level"] <= level:
            return False
    return False
