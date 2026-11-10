"""Statements from CVM's open data (sources/regulators.py): each filing version's balance sheet, income statement and
cash flows, the group's and the parent's, for the filing's own period and the earlier one it presents again. Every
line keeps CVM's account code and its name as filed, in Portuguese; an English name is added later where the
company's own translated form gives one (pipeline/regulators.py)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from ...checks.coded import cvm_checks
from ...domain.model import Reading
from ...domain.periods import Period, label_period

_SCALES = {"MIL": 1000.0, "UNIDADE": 1.0, "MILHAO": 1e6, "MILHÃO": 1e6}
_CURRENCIES = {"REAL": "BRL"}
_STATEMENT = {"BPA": "balance", "BPP": "balance", "DRE": "income", "DFC_MI": "cash_flow", "DFC_MD": "cash_flow"}
# CVM's fixed accounts that are a statement's key figures, named as the selection compares them (selection/figures.py).
_KEY_CONCEPTS = {("balance", "1"): "cvm_Assets", ("balance", "2"): "cvm_EquityAndLiabilities",
                 ("income", "3.01"): "cvm_Revenue", ("income", "3.11"): "cvm_ProfitLoss",
                 ("cash_flow", "6.05.02"): "cvm_CashAndCashEquivalents"}


def _order(code: str) -> tuple[int, ...]:
    return tuple(int(part) for part in code.split(".") if part.isdigit())


def cvm_readings(form: str, rows: dict[str, list[dict]], fiscal_year_end: str, year_offset: int) -> list[Reading]:
    """Readings of one year's CVM file for one company (form "ITR" or "DFP")."""
    filings = {(row["DT_REFER"], row["VERSAO"]): row for row in rows.get("index", [])}
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for key, part_rows in rows.items():
        if key == "index":
            continue
        part, scope = key.rsplit("_", 1)
        for row in part_rows:
            end = row["DT_FIM_EXERC"]
            groups[(row["DT_REFER"], row["VERSAO"], _STATEMENT[part], scope, row.get("DT_INI_EXERC") or "", end,
                    row["ORDEM_EXERC"])].append(row)
    readings = []
    for (refer, version, statement, scope, start, end, order), lines in groups.items():
        filing = filings.get((refer, version))
        if filing is None:
            continue
        period = Period(date.fromisoformat(end), date.fromisoformat(start) if start else None)
        labelled = label_period(period, fiscal_year_end, year_offset)
        if labelled is None:
            continue
        unit = _SCALES.get(lines[0]["ESCALA_MOEDA"].upper(), 1000.0)
        ordered = sorted(lines, key=lambda row: _order(row["CD_CONTA"]))
        values = {row["CD_CONTA"]: float(row["VL_CONTA"]) * unit for row in ordered}
        reading = Reading(
            statement=statement, duration=labelled[2], fiscal_year=labelled[0], fiscal_period=labelled[1],
            period_end=period.end, period_start=period.start, currency=_CURRENCIES.get(lines[0]["MOEDA"].upper()),
            accession=f"CVM-{form}-{filing['ID_DOC']}-v{version}", form=form,
            filed=date.fromisoformat(filing["DT_RECEB"][:10]),
            own=order.upper().startswith("ÚLTIMO") and end == refer, source="cvm", basis="IFRS",
            scope="consolidated" if scope == "con" else "parent",
            lines=[{"line": index, "concept": _KEY_CONCEPTS.get((statement, row["CD_CONTA"]), f"cvm_{row['CD_CONTA']}"),
                    "code": row["CD_CONTA"], "label": row["DS_CONTA"], "fixed": row.get("ST_CONTA_FIXA") == "S",
                    "level": min(20, row["CD_CONTA"].count(".")), "is_heading": False, "value": values[row["CD_CONTA"]]}
                   for index, row in enumerate(ordered)],
            checks=cvm_checks(statement, values, unit, {row['CD_CONTA'] for row in ordered if row.get('ST_CONTA_FIXA') == 'S'}))
        readings.append(reading)
    return readings
