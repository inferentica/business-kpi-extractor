"""One XBRL filing through Arelle, the reference XBRL processor, in a process of its own: its numeric facts and its
own calculation file's verdict (XBRL Calculations 1.1, round-to-nearest), printed as one line of JSON.

    python -m statement_extractor.checks.arelle_run <instance or inline document URL> <cache directory>

Run apart from the reader (checks/arelle.py) so a filing Arelle cannot load, or one that takes too long, costs that
filing's check and nothing else. Needs only Arelle and the standard library.
"""
from __future__ import annotations

import json
import os
import sys
import time


def _day(context, name: str) -> str | None:
    """A context's date as the filing writes it (Arelle's own datetimes run to the next midnight)."""
    for child in context.period.iterchildren():
        if getattr(child, "localName", None) == name:
            return (child.textValue or "").strip()[:10] or None
    return None


def _when(context) -> tuple[str | None, str | None]:
    if context.isInstantPeriod:
        return None, _day(context, "instant")
    if context.isStartEndPeriod:
        return _day(context, "startDate"), _day(context, "endDate")
    return None, None  # forever: no period a statement prints


def _concept(qname) -> str:
    return f"{qname.prefix}_{qname.localName}" if qname.prefix else qname.localName


def _number(raw) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def run(url: str, cache: str) -> dict:
    from arelle import XbrlConst
    from arelle.api.Session import Session
    from arelle.logging.handlers.StructuredMessageLogHandler import StructuredMessageLogHandler
    from arelle.RuntimeOptions import RuntimeOptions

    started = time.time()
    options = RuntimeOptions(entrypointFile=url, calcs="c11r", validate=True, keepOpen=True, cacheDirectory=cache,
                             httpUserAgent=os.environ.get("SEC_USER_AGENT", "TradeTracker admin@inferentica.com"))
    handler = StructuredMessageLogHandler()
    with Session() as session:
        session.run(options, logHandler=handler)
        models = session.get_models()
        if not models or not getattr(models[0], "contexts", None):
            return {"error": "not loaded", "seconds": round(time.time() - started, 1)}
        model = models[0]
        facts = []
        for fact in model.facts:
            if not fact.isNumeric or fact.isNil or fact.context is None:
                continue
            value = _number(fact.xValue)
            start, end = _when(fact.context)
            if value is None or end is None:
                continue
            facts.append([_concept(fact.qname), start, end, bool(fact.context.qnameDims), value,
                          fact.decimals if fact.decimals not in (None, "INF") else None])
        calc = []
        for record in handler.logRecordBuffer:
            if not str(getattr(record, "messageCode", "")).startswith("calc11e:inconsistentCalculation"):
                continue
            args = record.args if isinstance(record.args, dict) else {}
            context = model.contexts.get(str(args.get("contextID", "")))
            if context is None or " - Statement - " not in str(args.get("linkroleDefinition", "")):
                continue  # a note's or schedule's sum, not one of the statements' (EDGAR's role definitions)
            start, end = _when(context)
            calc.append([str(args.get("concept", "")).replace(":", "_", 1), start, end, bool(context.qnameDims),
                         str(args.get("reportedSum", "")), str(args.get("computedSum", "")),
                         str(args.get("linkrole", ""))])
        sums = sorted({_concept(relation.fromModelObject.qname)
                       for arcrole in XbrlConst.summationItems
                       for relation in model.relationshipSet(arcrole).modelRelationships
                       if relation.fromModelObject is not None
                       and " - Statement - " in model.roleTypeDefinition(relation.linkrole)})
        codes: dict[str, int] = {}
        for message in handler.messages:
            if message.get("levelname") in ("ERROR", "CRITICAL"):
                codes[str(message.get("messageCode"))] = codes.get(str(message.get("messageCode")), 0) + 1
    return {"facts": facts, "calc": calc, "sums": sums, "errors": codes, "seconds": round(time.time() - started, 1)}


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1], sys.argv[2])))
