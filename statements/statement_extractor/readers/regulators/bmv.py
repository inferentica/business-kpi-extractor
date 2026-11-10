"""Facts of a BMV quarterly report package (Mexico's exchange, under CNBV's taxonomy over the IFRS taxonomy): a zip
holding the report as JSON, its facts by context and unit. Only the statements' own facts are read: those without a
dimension, tagged from the IFRS taxonomy."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import date

from .tagged import Fact


def _day(text: str | None) -> date | None:
    return date.fromisoformat(text[:10]) if text else None


def bmv_facts(package: bytes) -> list[Fact]:
    archive = zipfile.ZipFile(io.BytesIO(package))
    report = json.loads(archive.read(next(name for name in archive.namelist() if name.endswith(".json"))))
    contexts, units = report.get("ContextosPorId") or {}, report.get("UnidadesPorId") or {}
    facts = []
    for fact in (report.get("HechosPorId") or {}).values():
        concept, context = fact.get("IdConcepto") or "", contexts.get(fact.get("IdContexto"))
        if not concept.startswith("ifrs-full_") or not context or context.get("ContieneInformacionDimensional"):
            continue
        try:
            value = float(fact.get("ValorNumerico"))
        except (TypeError, ValueError):
            continue
        period = context.get("Periodo") or {}
        end = _day(period.get("FechaFin") or period.get("FechaInstante"))
        if end is None:
            continue
        unit = units.get(fact.get("IdUnidad")) or {}
        measures = unit.get("Medidas") or []
        currency = next((str(m.get("Nombre") or m).rsplit(":", 1)[-1] for m in measures
                         if "iso4217" in str(m.get("EspacioNombres") or m)), None) if measures else None
        decimals = fact.get("Decimales")
        facts.append(Fact(concept[10:], _day(period.get("FechaInicio")), end, value, currency,
                          int(decimals) if decimals not in (None, "", "INF") else None))
    return facts
