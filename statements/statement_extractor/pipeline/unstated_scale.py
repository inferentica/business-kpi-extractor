"""A scale proven by the filing itself, for statements that print none. Icahn's 1997 10-Qs and Sylvan Learning's 1997
10-K print whole dollars with no caption; Ross's cash flow statements omit the "($000)" its other statements print. A
figure read at a guessed scale would be worse than none, so each such statement is read only at a scale an independent
figure of the same filing proves, at exactly one of units, thousands or millions:

- a balance sheet: its total assets are the EX-27 schedule's (stated with its <MULTIPLIER>), else its cash is the
  closing cash of the filing's cash flow statement on the same date (Copel's Q1 2004 report, round 87);
- an income statement: its net income is the EX-27 schedule's;
- a cash flow statement: its closing cash is the cash of the filing's balance sheet on the same date.

No proof, no statement."""
from __future__ import annotations

from ..checks.schedules import Schedule
from ..domain.model import PASS, Check, Reading
from ..selection.figures import key_figures
from ..selection.ties import cash_on_balance

_SCALES = (1.0, 1e3, 1e6)


def _schedule(reading: Reading, schedules: list[Schedule]) -> Schedule | None:
    for schedule in schedules:
        if not schedule.stated or abs((schedule.period_end - reading.period_end).days) > 7:
            continue
        if reading.statement == "income" and schedule.duration not in (None, reading.duration):
            continue
        return schedule
    return None


def _scaled(reading: Reading, scale: float) -> dict:
    return {"lines": [{**line, "value": None if line["value"] is None else line["value"] * scale} for line in reading.lines]}


def _fits(reading: Reading, schedules: list[Schedule], balances: list[Reading], flows: list[Reading] = ()) -> set[float]:
    """The scales at which the reading's proving figure holds."""
    if reading.statement in ("balance", "income"):
        schedule = _schedule(reading, schedules)
        figure = None if schedule is None else (schedule.total_assets if reading.statement == "balance"
                                                 else schedule.net_income)
        if figure is None and reading.statement == "balance":
            same_day = [flow for flow in flows if flow.period_end == reading.period_end]
            return {scale for scale in _SCALES if any(cash_on_balance({"lines": flow.lines}, _scaled(reading, scale)) == "agree"
                                                      for flow in same_day)}
        if figure is None:
            return set()
        values = [line["value"] for line in reading.lines if line["value"] is not None and not line["is_heading"]]
        return {scale for scale in _SCALES
                if any(abs(value * scale - figure) <= 0.5 * max(scale, schedule.multiplier) for value in values)}
    same_day = [balance for balance in balances if balance.period_end == reading.period_end]
    if key_figures("cash_flow", reading.lines).get("closing cash") is None:
        return set()
    return {scale for scale in _SCALES
            if any(cash_on_balance(_scaled(reading, scale), {"lines": balance.lines}) == "agree" for balance in same_day)}


def proven_scales(raw: list[Reading], schedules: list[Schedule], witnesses: list[Reading]) -> dict[str, float]:
    """Each statement kind's proven scale, from its own columns (every one with a proof must agree)."""
    found: dict[str, set[float]] = {}
    balances = [reading for reading in witnesses if reading.statement == "balance"]
    flows = [reading for reading in witnesses if reading.statement == "cash_flow"]
    for kind in ("balance", "income", "cash_flow"):
        for reading in (reading for reading in raw if reading.statement == kind and reading.own):
            fits = _fits(reading, schedules, balances, flows)
            if fits:
                found[kind] = found.get(kind, fits) & fits
        if kind == "balance" and len(found.get("balance", ())) == 1:
            scale = next(iter(found["balance"]))  # a proven balance sheet proves cash flows against it too
            balances += [Reading(**{**reading.__dict__, "lines": _scaled(reading, scale)["lines"]})
                         for reading in raw if reading.statement == "balance"]
    return {kind: next(iter(scales)) for kind, scales in found.items() if len(scales) == 1}


def with_proven_scale(read, content: str, schedules: list[Schedule], witnesses: list[Reading],
                      **kwargs) -> tuple[list[Reading], list[str]]:
    """The statements of a document that printed no scale, read at the scale the filing proves; (readings, notes)."""
    raw, _ = read(content, unstated_scale=1.0, **kwargs)
    scales = proven_scales(raw, schedules, witnesses)
    readings, notes = [], []
    for scale in sorted(set(scales.values())):
        kinds = sorted(kind for kind, value in scales.items() if value == scale)
        for reading in read(content, unstated_scale=scale, **kwargs)[0]:
            if reading.statement in kinds:
                reading.checks.append(Check("scale", PASS, f"no scale printed; x{scale:,.0f} proven by the filing"))
                readings.append(reading)
        notes.append(f"{', '.join(kinds)}: no scale printed; x{scale:,.0f} proven by the filing's own figures")
    return readings, notes
