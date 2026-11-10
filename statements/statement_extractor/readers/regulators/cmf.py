"""Facts of a CMF financial statements XBRL instance (Chile's regulator, under its CL-CI taxonomy over the IFRS
taxonomy). Only the statements' own facts are read: those in a context without a dimension, tagged from the IFRS
taxonomy."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date

from .tagged import Fact

_XBRLI = "{http://www.xbrl.org/2003/instance}"


def cmf_facts(instance: bytes) -> list[Fact]:
    root = ET.fromstring(instance)
    contexts, units = {}, {}
    for context in root.iter(_XBRLI + "context"):
        if context.find(f".//{_XBRLI}segment") is not None or context.find(f"{_XBRLI}scenario") is not None:
            continue
        period = context.find(_XBRLI + "period")
        instant = period.findtext(_XBRLI + "instant")
        start, end = period.findtext(_XBRLI + "startDate"), period.findtext(_XBRLI + "endDate") or instant
        contexts[context.get("id")] = (date.fromisoformat(start[:10]) if start else None, date.fromisoformat(end[:10]))
    for unit in root.iter(_XBRLI + "unit"):
        measure = unit.findtext(_XBRLI + "measure") or ""
        units[unit.get("id")] = measure.rsplit(":", 1)[-1] if measure.startswith("iso4217") else None
    facts = []
    for element in root:
        if not element.tag.startswith("{") or element.get("contextRef") not in contexts:
            continue
        namespace, name = element.tag[1:].split("}", 1)
        if not namespace.endswith("/ifrs-full"):  # the IFRS taxonomy's own elements, not CMF's extensions
            continue
        try:
            value = float(element.text)
        except (TypeError, ValueError):
            continue
        start, end = contexts[element.get("contextRef")]
        decimals = element.get("decimals")
        facts.append(Fact(name, start, end, value, units.get(element.get("unitRef")),
                          int(decimals) if decimals not in (None, "INF") else None))
    return facts
