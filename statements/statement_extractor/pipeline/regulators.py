"""A foreign filer's statements as its home regulator publishes them (sources/regulators.py): CVM's quarterly and
annual forms for Brazil's companies, OpenDART's periodic reports for Korea's, BMV's quarterly XBRL for Mexico's and
CMF's for Chile's (pipeline/latam.py). Their quarters stand above the 6-K
releases that translate them, their years beside the 20-F as its witness (selection/choose.py).

Each line keeps its name as filed and its code or tag, with an English name where one exists (decided 2026-11-11):
the IFRS taxonomy's label for a line tagged from it, and for a CVM account the company's own translated form, the
newest of its 6-K releases that prints CVM's codes on the chart in use since 2010."""
from __future__ import annotations

from datetime import date

from ..domain.ifrs_labels import english_names
from ..readers.regulators.cvm import cvm_readings
from ..readers.regulators.dart import dart_readings
from ..sources.regulators import DART_REPORTS, KEPT_SERVED, REGULATED, cvm_rows, dart_corp_code, dart_rows
from .latam import read_bmv, read_cmf
from .units import Units, readings_of, record_of, unit_id

_CVM_FIRST = {"ITR": 2011, "DFP": 2010}  # the first year of each form in CVM's open data
_DART_FIRST = 2015
_CHART_SINCE = date(2011, 1, 1)  # CVM's accounts were renumbered for IFRS in 2010
# The years a regulator's files still change in: this one and the last, as forms arrive and are refiled. Their units
# are always read again; older years are taken as read before (pipeline/units.py).
_OPEN_YEARS = 2


def read_regulators(symbol: str, since: date, fiscal_year_end: str, year_offset: int, printed: list,
                    log=print, units: Units | None = None) -> tuple[list[dict], list]:
    """(a record per year and form read, readings) of the company's regulator data since `since`; nothing for a
    company no regulator is registered for. With `units`, a year read before and closed since is taken as read."""
    regulator, identity = REGULATED.get(symbol.upper(), (None, ()))
    if regulator is None:
        return [], []
    records, readings = [], []
    this_year = date.today().year
    served_before = len(KEPT_SERVED)
    if regulator in ("bmv", "cmf"):
        records, readings = (read_bmv if regulator == "bmv" else read_cmf)(identity[0], since, fiscal_year_end, year_offset)
    elif regulator == "cvm":
        for form, first in _CVM_FIRST.items():
            for year in range(max(first, since.year), this_year + 1):
                record = {"regulator": "CVM", "form": form, "year": year}
                key, context = f"cvm-{form}-{year}", _context(fiscal_year_end, year_offset, identity)
                if (unit := _reused(units, key, context, year, this_year)) is not None:
                    records.append(record_of(unit))
                    readings.extend(readings_of(unit))
                    continue
                kept = len(KEPT_SERVED)
                try:
                    found = cvm_readings(form, cvm_rows(form, year, identity), fiscal_year_end, year_offset)
                except Exception as error:  # noqa: BLE001 - one year unread never stops the company
                    record["note"] = f"unreadable ({type(error).__name__}: {str(error)[:120]})"
                    records.append(_kept(units, key, context, record, [], year, complete=False))
                    continue
                record.update({"filings": len({reading.accession for reading in found}), "columns": len(found)})
                found = _kept_readings(units, key, context, record, found, year, len(KEPT_SERVED) == kept, records)
                readings.extend(found)
        _name_accounts(readings, _translated_accounts(printed))
    else:
        try:
            corp_code = dart_corp_code(identity[0])
        except Exception as error:  # noqa: BLE001
            log(f"{symbol}: OpenDART register unreadable ({type(error).__name__}: {str(error)[:120]})")
            return [{"regulator": "DART", "note": f"unreadable ({type(error).__name__})"}], []
        for year in range(max(_DART_FIRST, since.year), this_year + 1):
            for report in DART_REPORTS:
                record = {"regulator": "DART", "report": report, "year": year}
                key, context = f"dart-{report}-{year}", _context(fiscal_year_end, year_offset, identity)
                if (unit := _reused(units, key, context, year, this_year)) is not None:
                    records.append(record_of(unit))
                    readings.extend(readings_of(unit))
                    continue
                kept = len(KEPT_SERVED)
                try:
                    found = dart_readings(dart_rows(corp_code, year, report), year, report, "CFS",
                                          fiscal_year_end, year_offset)
                except Exception as error:  # noqa: BLE001
                    record["note"] = f"unreadable ({type(error).__name__}: {str(error)[:120]})"
                    records.append(_kept(units, key, context, record, [], year, complete=False))
                    continue
                record.update({"filings": len({reading.accession for reading in found}), "columns": len(found)})
                found = _kept_readings(units, key, context, record, found, year, len(KEPT_SERVED) == kept, records)
                readings.extend(found)
        _name_tags(readings, english_names())
    if len(KEPT_SERVED) > served_before:  # the regulator could not be reached: its last copies read instead
        records.append({"regulator": regulator.upper(), "note": "unreachable, kept copies read",
                        "kept_copies": KEPT_SERVED[served_before:]})
        log(f"{symbol}: {regulator.upper()} unreachable, {len(KEPT_SERVED) - served_before} kept copies read")
    kept = sum(1 for reading in readings if reading.status == "kept")
    log(f"{symbol}: {regulator.upper()} {len(readings)} columns from {len({reading.accession for reading in readings})} "
        f"filings, {kept} kept, {sum(1 for r in records if 'note' in r and 'kept_copies' not in r)} years unread")
    return records, readings


def _context(fiscal_year_end: str, year_offset: int, identity) -> dict:
    return {"fiscal_year_end": fiscal_year_end, "offset": year_offset, "identity": [str(part) for part in identity]}


def _reused(units: Units | None, key: str, context: dict, year: int, this_year: int) -> dict | None:
    if units is None or year > this_year - _OPEN_YEARS:
        return None
    return units.reuse(unit_id("regulator", key), context)


def _kept(units: Units | None, key: str, context: dict, record: dict, found: list, year: int,
          complete: bool = True) -> dict:
    if units is None:
        return record
    return record_of(units.keep("regulator", key, context, record, found, filed=f"{year}-12-31", complete=complete))


def _kept_readings(units: Units | None, key: str, context: dict, record: dict, found: list, year: int,
                   complete: bool, records: list) -> list:
    """The year's readings as its unit keeps them, before they are named in English (`_name_accounts`)."""
    if units is None:
        records.append(record)
        return found
    unit = units.keep("regulator", key, context, record, found, filed=f"{year}-12-31", complete=complete)
    records.append(record_of(unit))
    return readings_of(unit)


def _translated_accounts(printed: list) -> dict[str, str]:
    """CVM account code to the English name the company's own translated forms print, the newest form's."""
    names: dict[str, tuple[date, str]] = {}
    for reading in printed:
        if reading.form != "6-K" or reading.filed < _CHART_SINCE:
            continue
        for line in reading.lines:
            code = line.get("code")
            if code and (code not in names or names[code][0] < reading.filed):
                names[code] = (reading.filed, line["label"])
    return {code: label for code, (_filed, label) in names.items()}


def _name_accounts(readings: list, english: dict[str, str]) -> None:
    for reading in readings:
        for line in reading.lines:
            if line.get("fixed") and line["code"] in english:  # a fixed account means the same in every form
                line["label_en"] = english[line["code"]]


def _name_tags(readings: list, english: dict[str, str]) -> None:
    for reading in readings:
        for line in reading.lines:
            if line["concept"].startswith("ifrs-full_") and line["concept"][10:] in english:
                line["label_en"] = english[line["concept"][10:]]
