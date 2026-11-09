"""A statement's presented lines from its XBRL presentation: the filer's own rows in order, without taxonomy
scaffolding, each with the filer's label and, in a flow statement, which balance a balance line is. A breakdown is a
line only where the statement itself prints it."""
from __future__ import annotations

import re
from datetime import date

from ...domain.periods import Period, opening_instant


STATEMENT_METHODS = (("income", ("income_statement",)), ("balance", ("balance_sheet",)),
               ("cash_flow", ("cash_flow_statement", "cashflow_statement")))


STANDARD_LABEL = "http://www.xbrl.org/2003/role/label"


# Taxonomy scaffolding the filer did not title: "Statement [Table]", "Statement [Line Items]", "… [Abstract]".
_STRUCTURAL_LABEL = re.compile(r"\[(abstract|line items|table|axis|domain|member)\]\s*$", re.I)


_STRUCTURAL_CONCEPT = re.compile(r"(StatementTable|StatementLineItems|Axis|Domain|Member)$")


def clean_label(raw: str) -> str:
    """The filing's label, in sentence case where the filer wrote it in capitals (TSMC's "NET REVENUE")."""
    label = re.sub(r"\s+", " ", raw).strip()
    letters = [char for char in label if char.isalpha()]
    if len(letters) > 3 and all(char.isupper() for char in letters):
        label = label[0] + label[1:].lower()
    return label[:300]


def statement_of(xbrl, methods: tuple[str, ...]):
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


def presented_rows(statement, shown: set[str] | None = None) -> list[dict]:
    """The statement's rows. A row under a dimension is kept, marked with its members, when the statement's own
    presentation shows every axis and member it carries (Visa's earnings per share by class of stock, KKR's revenues
    by segment, Apple's products and services); edgartools also attaches breakdowns from the notes (Apple's revenue by
    segment and by country, JPM's VIEs), which the statement does not print."""
    rows = []
    for row in statement.get_raw_data() or []:
        if row.get("is_dimension") or row.get("dimension"):
            member = _printed_member(row, shown or set())
            if member is None:
                continue
            row = {**row, "_member": member}
        concept, label = str(row.get("concept") or ""), str(row.get("label") or "")
        if row.get("is_abstract") and (_STRUCTURAL_LABEL.search(label) or _STRUCTURAL_CONCEPT.search(concept)):
            continue
        if _STRUCTURAL_CONCEPT.search(concept):
            continue
        if any(re.search(r"\s", key) for key in (row.get("values") or {})):
            # Period keys with line breaks in them ("duration_\n2017-01-01\n_\n2017-12-31\n", Omega's 10-Ks of
            # 2015-18): the same periods, which every lookup by a period's key would otherwise miss.
            row = {**row, "values": {re.sub(r"\s+", "", key): value for key, value in row["values"].items()}}
        rows.append(row)
    return rows


def _printed_member(row: dict, shown: set[str]) -> str | None:
    dimensions = row.get("dimension_metadata") or []
    if not dimensions:
        return None
    for dimension in dimensions:
        axis = str(dimension.get("dimension") or "").replace(":", "_", 1)
        member = str(dimension.get("member") or "").replace(":", "_", 1)
        if axis not in shown or member not in shown:
            return None
    return " - ".join(clean_label(str(dimension.get("member_label") or dimension.get("member"))) for dimension in dimensions)


def is_instant_line(row: dict) -> bool:
    keys = list((row.get("values") or {}).keys())
    return bool(keys) and all(key.startswith("instant_") for key in keys)


def annotated(xbrl, rows: list[dict], flows: bool) -> list[dict]:
    """Each line with the filer's labels and, in a flow statement, which balance a balance line is: the opening one (by
    the role its presentation gives it, "beginning of period", else the first of a balance shown twice), the closing
    one (that same line shown again at the end), or another (a reconciliation after the closing balance: Coca-Cola's
    "less restricted cash" down to the balance sheet's cash). Each other balance is shown at the period's end."""
    balances = [index for index, row in enumerate(rows) if flows and is_instant_line(row) and not row.get("_member")]
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
    last: dict[str, str | None] = {}  # each concept's latest balance role, which its breakdowns below it share
    for index, row in enumerate(rows):
        balance, broken_down = None, None
        if index in concept_of:
            balance = "opening" if index in opening else "closing" if index == closing else "other"
            last[concept_of[index]] = balance
        elif row.get("_member") and flows and is_instant_line(row):
            broken_down = last.get(str(row["concept"]))  # KKR's opening and closing cash by segment
        annotated.append({**row, "_labels": _labels(xbrl, str(row["concept"])), "_balance": balance,
                          "_member_balance": broken_down, "_index": index})
    return annotated


def key_for(row: dict, period: Period, instants: dict[date, str]) -> str | None:
    """Which of a line's figures belongs in this column: the period's own, or for a balance shown in a flow statement
    (opening and closing cash), the instant the period opens or closes on."""
    if period.start is None or not row.get("_balance"):
        return period.key
    if row["_balance"] == "opening":
        return opening_instant(period, instants)
    return f"instant_{period.end}"


def label_for(row: dict) -> str:
    """The filer's own label in the role its presentation asks for (a total's, a period start's), else its standard
    one, else what edgartools shows."""
    labels = row.get("_labels") or {}
    role = row.get("preferred_label")
    return str((labels.get(role) if role else None) or labels.get(STANDARD_LABEL) or row.get("label") or row["concept"])
