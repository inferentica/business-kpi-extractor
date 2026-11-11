"""Prints what the XBRL reader makes of filings: the breakdowns it keeps, the ones it could not reconcile (with their
rows and total), and the company's own segment revenue measure. For finding why a filing's split was not stored. No AI,
nothing stored.

    python -m kpi_extractor.ops.xbrl_inspect --accessions 0000050863-22-000020[,...]

Runs on GitHub (the XBRL Inspect workflow): SEC answers requests from there with the identity the extractor uses."""
from __future__ import annotations

import argparse
import os
import time

from edgar import find, set_identity

from ..readers.segment_income import segment_revenue
from ..xbrl import extract_breakdowns, member_children, official_labels


def _amount(value: float) -> str:
    return f"{value / 1e6:,.1f}m"


def inspect(accession: str) -> None:
    filing = find(accession)
    print(f"\n== {accession} {getattr(filing, 'form', '')} {getattr(filing, 'company', '')} filed {getattr(filing, 'filing_date', '')}")
    xbrl = filing.xbrl()
    if xbrl is None:
        print("   no XBRL")
        return
    facts = xbrl.facts.to_dataframe()
    labels = official_labels(getattr(xbrl, "element_catalog", None))
    children = member_children(xbrl)
    breakdowns = extract_breakdowns(facts, xbrl.entity_info or {}, filing.form, labels, children)
    if breakdowns is None:
        print("   no document period")
        return
    print(f"   period end {breakdowns.period_end} annual={breakdowns.annual} revenue {_amount(breakdowns.total_revenue or 0)}")
    for group in breakdowns.groups:
        print(f"-- kept {group.key} ({group.label}) {group.concept} error {(group.reconciliation_error or 0):+.4f} total {_amount(group.total or 0)}")
        for key, label, value in group.members:
            print(f"     {key:<40} {label:<50} {_amount(value)}")
    for candidate in breakdowns.rejected:
        parts = sum(value for _label, value in candidate.members.values())
        print(f"-- not reconciled {candidate.key} ({candidate.label}) {candidate.concept} total {_amount(candidate.total or 0)} "
              f"rows {_amount(parts)}")
        for key, (label, value) in candidate.members.items():
            print(f"     {key:<40} {label:<50} {_amount(value)}")
    try:
        own = segment_revenue(facts, breakdowns.period_end, breakdowns.annual, labels, children)
    except Exception as error:  # noqa: BLE001 - printed, not raised
        own = f"error: {error}"
    print(f"-- own segment revenue measure: {own}")
    _segment_facts(facts, breakdowns.period_end)


def _segment_facts(facts, period_end) -> None:
    """The period's facts on any segment axis, by concept: how the filing tags its segments."""
    axes = [column for column in facts.columns if column.startswith("dim_") and "egment" in column]
    if not axes:
        counts = {column[4:]: int(facts[column].notna().sum()) for column in facts.columns if column.startswith("dim_")}
        print(f"-- no segment axis in the facts; axes used: {dict(sorted(counts.items(), key=lambda item: -item[1])[:15])}")
        return
    end = str(period_end)
    rows = facts[(facts["period_end"].astype(str).str[:10] == end) & facts[axes].notna().any(axis=1)]
    dims = [column for column in facts.columns if column.startswith("dim_")]
    print(f"-- facts on segment axes ending {end}: {len(rows)}")
    for concept, group in list(rows.groupby("concept"))[:25]:
        print(f"   {concept} ({len(group)})")
        for _index, row in group.head(12).iterrows():
            tags = ", ".join(f"{column[4:]}={row[column]}" for column in dims if isinstance(row[column], str))
            start = str(row.get("period_start") or "")[:10]
            print(f"      {start}..{end} {row.get('numeric_value')}  {tags}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accessions", required=True, help="Comma-separated accession numbers")
    args = parser.parse_args(argv)
    set_identity(os.environ.get("SEC_USER_AGENT", "TradeTracker admin@inferentica.com"))
    for accession in [item.strip() for item in args.accessions.split(",") if item.strip()]:
        try:
            inspect(accession)
        except Exception as error:  # noqa: BLE001 - one filing's failure does not stop the others
            print(f"\n== {accession}: {type(error).__name__}: {error}")
        time.sleep(0.5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
