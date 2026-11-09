"""A replay whose source was traced later carries the call that placed its source, down the whole chain."""
from kpi_extractor.steps.trace_links import TraceLinks


def _v(year, accession, locator):
    return {"group_key": "kpi_geo", "kpi_key": "taiwan", "fiscal_year": year, "fiscal_period": "Q2", "method": "ai",
            "validation_status": "verified", "source_accession": accession, "locator": locator, "value": 1.0}


class _Company(TraceLinks):
    def __init__(self, values):
        self.values = {(v["group_key"], v["kpi_key"], v["fiscal_year"], v["fiscal_period"]): v for v in values}
        self.symbol, self.logged = "TSM", []

    def log(self, line):
        self.logged.append(line)


def test_a_chain_of_replays_takes_the_call_of_its_traced_root():
    # TSMC: 2020's reading retraced (call c1); 2021 replays 2020, 2022 replays 2021, neither with a call.
    company = _Company([_v("2020", "a20", {"ai_call": "c1"}), _v("2021", "a21", {"replayed_from": "a20"}),
                        _v("2022", "a22", {"replayed_from": "a21"})])
    linked = {row["fiscal_year"]: row["locator"]["placed_by_call"] for row in company._link_replays()}
    assert linked == {"2021": "c1", "2022": "c1"}


def test_a_replay_whose_source_is_another_filing_or_untraced_stays_as_it_is():
    company = _Company([_v("2020", "a20", {}), _v("2021", "a21", {"replayed_from": "a20"}),
                        _v("2019", "x19", {"ai_call": "c0"}), _v("2023", "a23", {"replayed_from": "zz"})])
    assert company._link_replays() == []
