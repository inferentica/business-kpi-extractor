"""Mexico's and Chile's companies' quarters from their home filings (sources/latam.py), read as IFRS-tagged facts
(readers/regulators/tagged.py): each line's tag as filed, its name the IFRS taxonomy's English label."""
from __future__ import annotations

from datetime import date, timedelta

from ..domain.ifrs_labels import english_names
from ..domain.ifrs_roles import statement_roles
from ..readers.regulators.bmv import bmv_facts
from ..readers.regulators.cmf import cmf_facts
from ..readers.regulators.tagged import tagged_readings
from ..sources.latam import bmv_package, bmv_packages, cmf_statements

_CMF_FIRST = 2009  # Chile's listed companies moved to IFRS from 2009


def read_bmv(key: str, since: date, fiscal_year_end: str, year_offset: int) -> tuple[list[dict], list]:
    roles, english = statement_roles(), english_names()
    records, readings = [], []
    for package in bmv_packages(key):
        if package["end"] < since:
            continue
        record = {"regulator": "BMV", "file": package["file"], "received": str(package["received"])}
        try:
            found = tagged_readings(bmv_facts(bmv_package(package)), roles, english,
                                    accession=f"BMV-{package['id']}-v{package['version']}",
                                    form="BMV-FY" if package["audited"] else f"BMV-Q{package['end'].month // 3}",
                                    filed=package["received"] or package["end"], document_end=package["end"],
                                    fiscal_year_end=fiscal_year_end, year_offset=year_offset, source="bmv")
        except Exception as error:  # noqa: BLE001 - one package unread never stops the company
            record["note"] = f"unreadable ({type(error).__name__}: {str(error)[:120]})"
            records.append(record)
            continue
        records.append({**record, "columns": len(found)})
        readings.extend(found)
    return records, readings


def read_cmf(rut: str, since: date, fiscal_year_end: str, year_offset: int) -> tuple[list[dict], list]:
    roles, english = statement_roles(), english_names()
    records, readings = [], []
    last = date.today() - timedelta(days=40)  # a quarter's statements are due weeks after it closes
    for year in range(max(_CMF_FIRST, since.year), last.year + 1):
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            end = date(year, month, day)
            if end > last:
                break
            record = {"regulator": "CMF", "period": f"{end:%Y-%m}"}
            try:
                instance, filed = cmf_statements(rut, end)
                found = [] if instance is None else tagged_readings(
                    cmf_facts(instance), roles, english, accession=f"CMF-{rut}-{end:%Y%m}-{filed:%Y%m%d}",
                    form="CMF-FY" if month == 12 else f"CMF-Q{month // 3}", filed=filed, document_end=end,
                    fiscal_year_end=fiscal_year_end, year_offset=year_offset, source="cmf")
            except Exception as error:  # noqa: BLE001
                record["note"] = f"unreadable ({type(error).__name__}: {str(error)[:120]})"
                records.append(record)
                continue
            records.append({**record, "columns": len(found), "filed": str(filed) if instance else None})
            readings.extend(found)
    return records, readings
