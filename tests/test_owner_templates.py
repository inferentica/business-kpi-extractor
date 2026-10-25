"""An owner-edited KPI list (Bedrock's KPI templates): additions re-read only recent quarters."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from kpi_extractor.ai import Spec
from kpi_extractor.pipeline import SymbolPipeline
from kpi_extractor.sec import FilingRef


def _spec(*kpis: str) -> Spec:
    return Spec.model_validate({"groups": [{"key": "users", "label": "Users", "kind": "metric",
                                            "kpis": [{"key": key, "label": key.title(), "unit": "count"} for key in kpis]}]})


def _release(accession: str, period_end: date) -> FilingRef:
    return FilingRef(accession=accession, form="8-K", filed=period_end, role="earnings_release", source_url="", period_end=period_end)


def _pipeline(filings: dict, quarters: int = 20) -> SimpleNamespace:
    carried: list[str] = []
    fake = SimpleNamespace(symbol="X", filings=filings, carried=carried, quarters=quarters, log=lambda message: None)
    fake._carry_to_version = lambda ref, version: carried.append(ref.accession)
    return fake


def test_owner_additions_keep_older_quarters_and_reread_the_last_year():
    releases = [_release(f"a{index}", date(2024 + index // 4, 3 * (index % 4) + 1, 28)) for index in range(8)]
    fake = _pipeline({ref.accession: {"spec_version": 3, "status": "done"} for ref in releases})
    SymbolPipeline._carry_owner_additions(fake, _spec("dau", "mau"), 4, {"version": 3, "groups": _spec("dau").model_dump()["groups"]}, releases)
    # Quarters within 370 days of the newest are read again (the last four, plus the year-ago quarter each new reading is
    # checked against); the older ones keep their readings and move to the new version.
    assert fake.carried == ["a0", "a1", "a2"]


def test_a_removal_or_change_is_not_carried():
    releases = [_release(f"a{index}", date(2024 + index // 4, 3 * (index % 4) + 1, 28)) for index in range(8)]
    fake = _pipeline({ref.accession: {"spec_version": 3, "status": "done"} for ref in releases})
    SymbolPipeline._carry_owner_additions(fake, _spec("dau"), 4, {"version": 3, "groups": _spec("dau", "mau").model_dump()["groups"]}, releases)
    assert fake.carried == []


def test_a_history_read_reads_owner_additions_in_every_quarter():
    # Read filings (40 quarters): a KPI's history, e.g. Meta's Facebook DAUs to 2023, is read, not carried.
    releases = [_release(f"a{index}", date(2024 + index // 4, 3 * (index % 4) + 1, 28)) for index in range(8)]
    fake = _pipeline({ref.accession: {"spec_version": 3, "status": "done"} for ref in releases}, quarters=40)
    SymbolPipeline._carry_owner_additions(fake, _spec("dau", "mau"), 4, {"version": 3, "groups": _spec("dau").model_dump()["groups"]}, releases)
    assert fake.carried == []
