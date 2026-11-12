import json
from datetime import date
from types import SimpleNamespace

from kpi_extractor import ai
from kpi_extractor.ai import Spec
from kpi_extractor.documents.document import Table, parse_document
from kpi_extractor.readers.extract import read_values
from kpi_extractor.pipeline import SymbolPipeline
from kpi_extractor.readers.replay import replay, reported_period, row_gone

URL = "https://www.sec.gov/Archives/q{n}report.htm"


def report(q, n2, n3, n5, others, members, extra_row=""):
    total = n2 + n3 + n5 + others + (7 if extra_row else 0)
    return f"""
<p>Quarterly report for the three months ended {q}, 2026 (Amounts in Thousands of New Taiwan Dollars)</p>
<table>
  <tr><td></td><td>Three Months Ended</td></tr>
  <tr><td>Resolution</td><td>2026</td><td>2025</td></tr>
  {extra_row}
  <tr><td>2-nanometer</td><td>{n2}</td><td>1</td></tr>
  <tr><td>3-nanometer</td><td>{n3}</td><td>2</td></tr>
  <tr><td>5-nanometer</td><td>{n5}</td><td>3</td></tr>
  <tr><td>Others</td><td>{others}</td><td>4</td></tr>
  <tr><td>Wafer revenue</td><td>{total}</td><td>10</td></tr>
</table>
<p>Paid memberships reached {members} million in the quarter, up 12% from a year ago.</p>
"""


SPEC = Spec.model_validate({"groups": [
    {"key": "technology", "label": "Revenue by Technology", "kind": "revenue_breakdown",
     "kpis": [{"key": "n3", "label": "3nm", "unit": "currency"}]},
    {"key": "operating", "label": "Operating Metrics", "kind": "metric",
     "kpis": [{"key": "members", "label": "Paid Memberships", "unit": "count"}]},
]})
Q1, Q2 = date(2026, 3, 31), date(2026, 6, 30)


def _first_quarter(document):
    table = next(iter(document.tables))
    block = next(key for key, item in document.blocks.items() if "Paid memberships" in item.text)
    return ai.Located.model_validate({"period_end": "2026-03-31", "tables": [
        {"group": "technology", "table": table, "col": 1, "first_row": 2, "last_row": 5, "total_row": 6}],
        "values": [{"kpi": "operating.members", "block": block, "quote": "Paid memberships reached 300 million",
                    "value_text": "300 million"}]})


def _history(document):
    items, problems = read_values(document, SPEC, _first_quarter(document), "TWD")
    assert problems == []
    return [{"group_key": f"kpi_{item.group.key}", "kpi_key": item.kpi.key, "fiscal_year": "2026", "fiscal_period": "Q1",
             "period_end": "2026-03-31", "method": "ai", "validation_status": "verified", "value": item.value,
             "locator": item.locator, "group_label": item.group.label, "kpi_label": item.kpi.label,
             "source_url": URL.format(n=1), "source_accession": "q1"} for item in items if not item.is_total]


class NoAi:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def ai(self, symbol, purpose, system, user, thinking, model="flash", prompt_version=None):
        # Every call is Flash; a reasoning call (the second reading, a review) answers as "reasoning".
        reader = "reasoning" if thinking else model
        self.calls.append((purpose, reader))
        return json.dumps(self.answers[(purpose, reader)])

    def call(self, operation, **payload):
        return {}


def _pipeline(control, values, notes=("read by AI",)):
    pipeline = SymbolPipeline(control, "TSM", quarters=4, force=False, log=lambda *_: None)
    pipeline.profile = SimpleNamespace(name="TSMC", fiscal_year_end="1231", foreign=True)
    pipeline.values = {(v["group_key"], v["kpi_key"], v["fiscal_year"], v["fiscal_period"]): v for v in values}
    pipeline.filings = {"q1": {"accession": "q1", "spec_version": 1, "status": "processed", "notes": list(notes)}}
    return pipeline


def test_next_quarter_is_read_again_by_code_with_new_rows_and_new_numbers():
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    second = parse_document(report("June 30", 11, 25, 31, 42, 310, "<tr><td>A16</td><td>7</td><td>-</td></tr>"),
                            URL.format(n=2))
    last = {f"{v['group_key'][4:]}.{v['kpi_key']}": v["locator"] for v in _history(first)}
    located = replay(second, last, Q2)
    assert located is not None and located.period_end == "2026-06-30"
    values, problems = read_values(second, SPEC, located, "TWD")
    assert problems == []
    read = {item.kpi.key: item.value for item in values if not item.is_total}
    assert read["members"] == 310_000_000
    # The listed 3nm KPI keeps its key; the new A16 row above the old first row is read, as the rows then add up to
    # the wafer revenue the report states (TSMC's 3-nanometer row headed its table in 2023 and was dropped).
    assert read["n3"] == 25_000 and read["a16"] == 7_000


def test_a_replayed_quarter_needs_no_ai_call():
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    second = parse_document(report("June 30", 11, 25, 31, 42, 310), URL.format(n=2))
    control = NoAi()
    pipeline = _pipeline(control, _history(first))
    read, problems, _ = pipeline._read(lambda: "prompt", second, SPEC, Q2, version=1)
    assert control.calls == [] and problems == [] and pipeline.result.replayed_reads == 1
    assert {item.kpi.key: item.value for item in read if not item.is_total}["r2nanometer"] == 11_000
    assert pipeline.result.replay_outcomes == {"replayed": len(SPEC.groups)}


def test_each_group_the_ai_reads_instead_says_why():
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    six = report("June 30", 21, 45, 61, 82, 310).replace("Three Months Ended", "Six Months Ended")
    flash = {"period_end": "2026-06-30", "values": []}
    pipeline = _pipeline(NoAi({("locate", "flash"): flash, ("locate", "reasoning"): flash}), _history(first))
    pipeline._read(lambda: "prompt", parse_document(six, URL.format(n=2)), SPEC, Q2, version=1)
    # The table's group is read by the AI, the sentence's group (its column header does not matter) by code.
    assert pipeline.result.replay_outcomes == {"column header changed": 1, "replayed": 1}
    fresh = _pipeline(NoAi({("locate", "flash"): flash, ("locate", "reasoning"): flash}), [])
    fresh._read(lambda: "prompt", parse_document(six, URL.format(n=2)), SPEC, Q2, version=1)
    assert fresh.result.replay_outcomes == {"no earlier reading of this kind of document": len(SPEC.groups)}


def test_replay_is_refused_when_a_number_repeats_but_holds_across_a_new_list_version():
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    flash = {"period_end": "2026-06-30", "values": []}
    answers = {("locate", "flash"): flash, ("locate", "reasoning"): flash}
    history = _history(first)
    same = parse_document(report("June 30", 10, 20, 30, 40, 300), URL.format(n=2))  # last quarter's numbers again
    control = NoAi(answers)
    _pipeline(control, history)._read(lambda: "prompt", same, SPEC, Q2, version=1)
    assert ("locate", "flash") in control.calls
    control = NoAi(answers)
    second = parse_document(report("June 30", 11, 25, 31, 42, 310), URL.format(n=2))
    read, _, _ = _pipeline(control, history)._read(lambda: "prompt", second, SPEC, Q2, version=2)
    # A new KPI list version with this group unchanged: the group is still read by code, with no AI.
    assert control.calls == [] and len(read) > 0


def test_kpis_missing_last_quarter_are_looked_for_by_the_ai_every_other_quarter():
    spec = Spec.model_validate({"groups": [*SPEC.model_dump()["groups"][:1], {
        "key": "operating", "label": "Operating Metrics", "kind": "metric",
        "kpis": [{"key": "members", "label": "Paid Memberships", "unit": "count"},
                 {"key": "stores", "label": "Stores", "unit": "count"}]}]})
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    second = parse_document(report("June 30", 11, 25, 31, 42, 310), URL.format(n=2))
    flash = {"period_end": "2026-06-30", "values": []}
    control = NoAi({("locate", "flash"): flash, ("locate", "reasoning"): flash})
    _pipeline(control, _history(first), notes=("read by AI", "not reported: operating.stores"))._read(
        lambda: "prompt", second, spec, Q2, version=1)
    assert control.calls == []
    control = NoAi({("locate", "flash"): flash, ("locate", "reasoning"): flash})
    _pipeline(control, _history(first), notes=("read by replay", "not reported: operating.stores"))._read(
        lambda: "prompt", second, spec, Q2, version=1)
    assert ("locate", "flash") in control.calls
    # In the newest release, a KPI never read anywhere (listed by the audit from that release) is looked for.
    control = NoAi({("locate", "flash"): flash, ("locate", "reasoning"): flash})
    pipeline = _pipeline(control, _history(first), notes=("read by AI", "not reported: operating.stores"))
    pipeline._newest_release = Q2
    pipeline._read(lambda: "prompt", second, spec, Q2, version=1)
    assert ("locate", "flash") in control.calls


def test_a_prior_year_column_is_never_replayed():
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    last = {f"{v['group_key'][4:]}.{v['kpi_key']}": {**v["locator"], "col": 2} for v in _history(first)
            if "quote" not in v["locator"]}
    second = parse_document(report("June 30", 11, 25, 31, 42, 310), URL.format(n=2))
    assert replay(second, last, Q2) == "column header changed"


def test_the_stated_quarter_end_wins_over_the_nominal_one():
    document = parse_document("<p>Results for the quarter ended June 27, 2026. As of June 27, 2026.</p>", URL.format(n=2))
    assert reported_period(document, Q2) == date(2026, 6, 27)


def test_a_kind_of_document_both_models_found_empty_is_read_by_flash_alone():
    document = parse_document("<p>Monthly revenue report for June.</p>", URL.format(n=2))
    empty = {"period_end": "2026-06-30", "values": []}
    control = NoAi({("locate", "flash"): empty})
    pipeline = _pipeline(control, [])
    pipeline.filings = {"q1": {"accession": "q1", "spec_version": 1, "status": "skipped", "value_count": 0,
                               "notes": ["read by AI"], "source_url": URL.format(n=1), "period_end": "2026-03-31"}}
    read, _, _ = pipeline._read(lambda: "prompt", document, SPEC, Q2, version=1)
    assert read == [] and control.calls == [("locate", "flash")]


def test_a_partial_flash_reading_of_an_empty_kind_still_gets_a_second_reading():
    document = parse_document("<p>Quarterly report.</p><table><tr><td>Wafer</td><td>5</td></tr></table>", URL.format(n=2))
    table = next(iter(document.tables))
    partial = {"period_end": "2026-06-30", "tables": [
        {"group": "technology", "table": table, "col": 1, "first_row": 0, "last_row": 0}]}
    control = NoAi({("locate", "flash"): partial, ("locate", "reasoning"): {"period_end": "2026-06-30"}})
    pipeline = _pipeline(control, [])
    pipeline.filings = {"q1": {"accession": "q1", "spec_version": 1, "status": "skipped", "value_count": 0,
                               "notes": ["read by AI"], "source_url": URL.format(n=1), "period_end": "2026-03-31"}}
    pipeline._read(lambda: "prompt", document, SPEC, Q2, version=1)
    assert ("locate", "reasoning") in control.calls


def test_a_six_month_column_is_never_replayed_as_the_quarter():
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    last = {f"{v['group_key'][4:]}.{v['kpi_key']}": v["locator"] for v in _history(first)}
    six = report("June 30", 21, 45, 61, 82, 310).replace("Three Months Ended", "Six Months Ended")
    assert replay(parse_document(six, URL.format(n=2)), last, Q2) == "column header changed"


def test_a_quote_about_another_node_is_never_taken_for_this_one():
    document = parse_document("<p>In the quarter, 3-nanometer shipments accounted for 28% of wafer revenue.</p>"
                              "<p>5-nanometer process technology accounted for 35%.</p>", URL.format(n=1))
    block = next(key for key, item in document.blocks.items() if "3-nanometer" in item.text)
    last = {"technology.n3": {"block": block, "quote": "3-nanometer shipments accounted for 28%", "value_text": "28%"}}
    changed = parse_document("<p>In the quarter, 5-nanometer shipments accounted for 35% of wafer revenue.</p>",
                             URL.format(n=2))
    assert replay(changed, last, Q2) == "sentence not found"


def test_a_six_month_column_written_in_digits_is_never_replayed():
    first = parse_document(report("March 31", 10, 20, 30, 40, 300).replace("Three Months Ended", "3 Months Ended"),
                           URL.format(n=1))
    last = {f"{v['group_key'][4:]}.{v['kpi_key']}": v["locator"] for v in _history(first)}
    six = report("June 30", 21, 45, 61, 82, 310).replace("Three Months Ended", "6 Months Ended")
    assert replay(parse_document(six, URL.format(n=2)), last, Q2) == "column header changed"


def test_a_quote_about_3_nm_never_becomes_5_nm():
    document = parse_document("<p>Revenue from 3 nm technology accounted for 28% of wafer revenue.</p>", URL.format(n=1))
    block = next(iter(document.blocks))
    last = {"technology.n3": {"block": block, "quote": "3 nm technology accounted for 28%", "value_text": "28%"}}
    changed = parse_document("<p>Revenue from 5 nm technology accounted for 35% of wafer revenue.</p>", URL.format(n=2))
    assert replay(changed, last, Q2) == "sentence not found"


def test_a_reading_without_header_evidence_goes_back_to_the_ai():
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    last = {f"{v['group_key'][4:]}.{v['kpi_key']}": {k: x for k, x in v["locator"].items() if k != "header"}
            for v in _history(first) if "quote" not in v["locator"]}
    assert replay(parse_document(report("June 30", 11, 25, 31, 42, 310), URL.format(n=2)), last, Q2) == "column header not recorded"


def test_a_reading_of_the_groups_code_left_is_placed_against_those_groups_alone():
    # Code replayed the table; Flash reads only the operating group, from last quarter's sentence. The table's KPIs are
    # not "missing now" from that reading: they were never asked of it.
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    second = parse_document(report("June 30", 11, 25, 31, 42, 310), URL.format(n=2))
    operating = Spec.model_validate({"groups": SPEC.model_dump()["groups"][1:]})
    block = next(key for key, item in second.blocks.items() if "Paid memberships" in item.text)
    located = ai.Located.model_validate({"period_end": "2026-06-30", "values": [
        {"kpi": "operating.members", "block": block, "quote": "Paid memberships reached 310 million",
         "value_text": "310 million"}]})
    read, problems = read_values(second, operating, located, "TWD")
    assert problems == []
    assert _pipeline(NoAi(), _history(first))._why_not_alone(located, read, operating, Q2, second) is None


def test_a_metric_kpi_code_cannot_find_again_is_the_only_one_the_ai_reads():
    spec = Spec.model_validate({"groups": [*SPEC.model_dump()["groups"][:1], {
        "key": "operating", "label": "Operating Metrics", "kind": "metric",
        "kpis": [{"key": "members", "label": "Paid Memberships", "unit": "count"},
                 {"key": "stores", "label": "Stores", "unit": "count"}]}]})
    first = parse_document(report("March 31", 10, 20, 30, 40, 300) + "<p>We ended the quarter with 812 stores.</p>",
                           URL.format(n=1))
    located = _first_quarter(first)
    stores = next(key for key, item in first.blocks.items() if "812 stores" in item.text)
    located.values.append(ai.Locator(kpi="operating.stores", block=stores, quote="We ended the quarter with 812 stores",
                                     value_text="812"))
    items, problems = read_values(first, spec, located, "TWD")
    assert problems == []
    history = [{"group_key": f"kpi_{item.group.key}", "kpi_key": item.kpi.key, "fiscal_year": "2026", "fiscal_period": "Q1",
                "period_end": "2026-03-31", "method": "ai", "validation_status": "verified", "value": item.value,
                "locator": item.locator, "group_label": item.group.label, "kpi_label": item.kpi.label,
                "source_url": URL.format(n=1), "source_accession": "q1"} for item in items if not item.is_total]
    # This quarter the stores sentence is worded anew: code reads the table and the members sentence, the AI the stores.
    second = parse_document(report("June 30", 11, 25, 31, 42, 310) + "<p>Our store count reached 830 at quarter end.</p>",
                            URL.format(n=2))
    block = next(key for key, item in second.blocks.items() if "830" in item.text)
    asked = []
    flash = {"period_end": "2026-06-30", "values": [{"kpi": "operating.stores", "block": block,
                                                     "quote": "Our store count reached 830", "value_text": "830"}]}
    pipeline = _pipeline(NoAi({("locate", "flash"): flash, ("locate", "reasoning"): flash}), history)

    def text(subset=None, hints=True):
        asked.append(sorted(f"{group.key}.{kpi.key}" for group in (subset or spec).groups for kpi in group.kpis))
        return "prompt"
    read, _, _ = pipeline._read(text, second, spec, Q2, version=1)
    assert asked[0] == ["operating.stores"]
    values = {f"{item.group.key}.{item.kpi.key}": item for item in read if not item.is_total}
    assert values["operating.members"].value == 310e6 and "replayed_from" in values["operating.members"].locator
    assert values["operating.stores"].value == 830
    # Both carry the list's own group, as the release flow expects when it orders groups by their place in the list.
    assert all(spec.groups.index(item.group) >= 0 for item in read)
    assert pipeline.result.replay_outcomes == {"replayed": 2, "sentence not found": 1}


def test_a_column_named_for_its_quarter_replays_only_as_that_quarter():
    def quarter(name, q, *numbers):
        return parse_document(report(q, *numbers).replace("Three Months Ended", name), URL.format(n=1 if name == "Q1" else 2))
    first = quarter("Q1", "March 31", 10, 20, 30, 40, 300)
    last = {f"{v['group_key'][4:]}.{v['kpi_key']}": v["locator"] for v in _history(first) if "quote" not in v["locator"]}
    assert not isinstance(replay(quarter("Q2", "June 30", 11, 25, 31, 42, 310), last, Q2, 2), str)
    # A Q2 filing whose column is still headed Q1 is not this quarter's column.
    assert replay(quarter("Q1", "June 30", 11, 25, 31, 42, 310), last, Q2, 2) == "column header changed"


def test_a_header_compares_without_its_quarters_name_or_its_dates_form():
    from kpi_extractor.readers.replay import comparable_header
    same = [("<first quarter>", "<second quarter>"), ("three months ended @ # #", "three months ended @ #"),
            ("three months endedseptember #", "three months ended @ #"), ("quarterly trends #q#", "quarterly trends <third quarter>")]
    assert all(comparable_header(a) == comparable_header(b) for a, b in same)
    assert comparable_header("three months ended @ #") != comparable_header("six months ended @ #")


def _replayed(before: str, value: str, now: str):
    document = parse_document(f"<p>{now}</p>", URL.format(n=2))
    block = next(iter(document.blocks))
    found = replay(document, {"operating.members": {"block": block, "quote": before, "value_text": value}}, Q2)
    return found if isinstance(found, str) else found.values[0].value_text


def test_a_sentences_dates_and_other_figures_change_with_the_quarter():
    assert _replayed("DAP was 2.26 billion on average for December 2019, an increase of 11% year-over-year.", "2.26 billion",
                     "DAP was 2.36 billion on average for March 2020, an increase of 12% year-over-year.") == "2.36 billion"
    assert _replayed("ARR increased $435 million quarter-over-quarter to $10.69 billion", "$10.69 billion",
                     "ARR increased $518 million quarter-over-quarter to $11.21 billion") == "$11.21 billion"
    assert _replayed("Perpetual Capital AUM as of 3Q'22 was comprised of Athene ($229 billion)", "$229 billion",
                     "Perpetual Capital AUM as of 4Q'22 was comprised of Athene ($236 billion)") == "$236 billion"
    assert _replayed("$48bn $97bn$153bn", "$153bn", "$50bn $90bn$156bn") == "$156bn"


def test_a_value_that_repeats_in_its_sentence_is_never_replayed():
    # Which 192 was read (last quarter's column or this quarter's) is not recorded: the AI reads it again.
    assert _replayed("Equity1 189 192 192 1.6% 792 761", "192", "Equity1 189 192 201 6.3% 802 773") == "value repeats in the sentence"


def test_a_renamed_report_is_replayed_from_the_quarter_before_under_its_old_name():
    # TSMC's report was tsmc2025q2consolidatedfina.htm, then a2026q2consolidatedreport-.htm: the same table, a new name.
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    renamed = parse_document(report("June 30", 11, 25, 31, 42, 310), "https://www.sec.gov/Archives/a2026q2consolidatedreport-.htm")
    control = NoAi()  # any AI call would fail: none is expected
    pipeline = _pipeline(control, _history(first))
    read, problems, _ = pipeline._read(lambda: "prompt", renamed, SPEC, Q2, version=1)
    assert control.calls == [] and problems == [] and pipeline.result.replayed_reads == 1
    # A renamed document whose table is not there goes to the AI as before.
    flash = {"period_end": "2026-06-30", "values": []}
    control = NoAi({("locate", "flash"): flash, ("locate", "reasoning"): flash})
    other = parse_document("<p>Board resolutions of June 30, 2026.</p>", "https://www.sec.gov/Archives/a2026q2board.htm")
    _pipeline(control, _history(first))._read(lambda: "prompt", other, SPEC, Q2, version=1)
    assert ("locate", "flash") in control.calls


def test_a_quote_replayed_into_a_full_year_sentence_is_not_the_quarters():
    # Meta Q4 2023: last quarter's "the average price per ad decreased by 6%" matched only the full year's sentence (its
    # quarter rose), which replay served as the quarter's -9%.
    from tests.readers.test_extract import META_Q4_2023
    document = parse_document(META_Q4_2023, "https://www.sec.gov/Archives/meta-12312023xexhibit991.htm")
    spec = Spec.model_validate({"groups": [{"key": "operating", "label": "Operating Metrics", "kind": "metric", "kpis": [
        {"key": "price", "label": "Average Price per Ad Year over Year Growth", "unit": "percent"}]}]})
    last = {"operating.price": {"block": "A_P2", "quote": "the average price per ad decreased by 6% year-over-year", "value_text": "6%"}}
    located = replay(document, last, date(2023, 12, 31), 4)
    values, problems = read_values(document, spec, located, "USD") if not isinstance(located, str) else ([], [located])
    assert values == [] and problems


def _jpm(newest: str, older: str, advisory: str) -> str:
    return f"""<table>
<tr><td>JPMORGAN CHASE &amp; CO.</td></tr><tr><td>COMMERCIAL &amp; INVESTMENT BANK</td></tr>
<tr><td>FINANCIAL HIGHLIGHTS, CONTINUED</td></tr><tr><td>(in millions, except where otherwise noted)</td></tr>
<tr><td></td><td>QUARTERLY TRENDS</td><td></td><td>SIX MONTHS ENDED JUNE 30,</td></tr>
<tr><td></td><td></td><td>{newest} Change</td><td></td></tr>
<tr><td></td><td>{newest}</td><td>{older}</td><td>2024</td></tr>
<tr><td>BUSINESS METRICS</td><td></td><td></td><td></td></tr>
<tr><td>Advisory</td><td>{advisory}</td><td>$598</td><td>$1,383</td></tr>
</table>"""


def test_a_table_whose_date_row_sits_below_its_title_rows_is_replayed_as_the_newest_quarter():
    # JPMorgan's supplement opens each table with four title rows: the "2Q24 1Q24" row was past the six rows the period
    # checks read, so no table of it was replayed (631 figures to the AI).
    spec = Spec.model_validate({"groups": [{"key": "fees", "label": "Investment Banking Fees", "kind": "metric", "kpis": [
        {"key": "advisory", "label": "Advisory", "unit": "currency"}]}]})
    url = "https://www.sec.gov/Archives/a{n}q24erfex992supplement.htm"
    second = parse_document(_jpm("2Q24", "1Q24", "$785"), url.format(n=2))
    last = {"fees.advisory": {"table": "T0", "row": 8, "col": 1, "row_label": "Advisory", "header": "quarterly trends"}}
    located = replay(second, last, date(2024, 6, 30), 2)
    values, problems = read_values(second, spec, located, "USD")
    assert problems == [] and [item.value for item in values] == [785_000_000]
    # A column that names another quarter is never read as this one, however deep its name sits.
    stale = parse_document(_jpm("1Q24", "4Q23", "$598"), url.format(n=2))
    assert replay(stale, last, date(2024, 6, 30), 2) == "column header changed"


def test_a_figure_the_owner_corrected_never_lends_its_place():
    # JPMorgan: the owner corrected firmwide client advisors to 9,107, but the figure kept the place it was misread at
    # (the Global Private Bank row, 3,536), and replay read that row again as next quarter's figure.
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    second = parse_document(report("June 30", 11, 25, 31, 42, 310), URL.format(n=2))
    history = [{**value, "owner_decision": "corrected", "value": 999} if value["kpi_key"] == "members" else value
               for value in _history(first)]
    pipeline = _pipeline(NoAi({("locate", "flash"): {"period_end": "2026-06-30", "values": []},
                               ("locate", "reasoning"): {"period_end": "2026-06-30", "values": []}}), history)
    read, _problems, _ = pipeline._read(lambda: "prompt", second, SPEC, Q2, version=1)
    assert "members" not in {item.kpi.key for item in read}
    assert pipeline.result.replay_outcomes.get("group not read in this kind of document before") == 1


def test_a_place_last_read_as_the_years_figure_is_never_replayed_as_a_quarter():
    # TSMC: its FY 2022 statements' "Wafer | Shipments (Piece)" row was replayed into the 2023 statements, and the year's
    # 12,002,177 wafers were taken for the fourth quarter's.
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    second = parse_document(report("June 30", 11, 25, 31, 42, 310), URL.format(n=2))
    history = [{**value, "fiscal_period": "FY"} if value["kpi_key"] == "members" else value for value in _history(first)]
    flash = {"period_end": "2026-06-30", "values": []}
    pipeline = _pipeline(NoAi({("locate", "flash"): flash, ("locate", "reasoning"): flash}), history)
    read, _problems, _ = pipeline._read(lambda: "prompt", second, SPEC, Q2, version=1)
    assert "members" not in {item.kpi.key for item in read}
    assert pipeline.result.replay_outcomes["last reading was the year's"] == 1
    assert {item.kpi.key for item in read if not item.is_total} >= {"n3"}  # the quarter's table still replays


def test_a_filing_giving_a_kpis_quarter_and_year_on_one_day_lends_the_quarters_place():
    from kpi_extractor.steps.shared import _later_place
    quarter = {"period_end": "2025-12-31", "fiscal_period": "Q4"}
    year = {"period_end": "2025-12-31", "fiscal_period": "FY"}
    assert _later_place(quarter, year) and not _later_place(year, quarter)
    assert _later_place({"period_end": "2026-03-31", "fiscal_period": "Q1"}, quarter) and _later_place(year, None)


def test_a_quarters_place_is_replayed_though_the_years_figure_shares_its_day():
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    second = parse_document(report("June 30", 11, 25, 31, 42, 310), URL.format(n=2))
    history = _history(first)
    year = [{**value, "fiscal_period": "FY", "locator": {"derived": True}} for value in history if value["kpi_key"] == "members"]
    pipeline = _pipeline(NoAi(), year + history)  # the year's figure first, as a store may hold it
    read, _problems, _ = pipeline._read(lambda: "prompt", second, SPEC, Q2, version=1)
    assert {item.kpi.key: item.value for item in read if not item.is_total}["members"] == 310_000_000
    assert "last reading was the year's" not in pipeline.result.replay_outcomes


def _members(count: str, scale_words: str) -> str:
    return f"""<p>People served by our businesses{scale_words}</p>
<table><tr><td></td><td>June 30, 2026</td><td>March 31, 2026</td></tr>
<tr><td>Medicare Advantage</td><td>{count}</td><td>7,800</td></tr></table>"""


def test_a_replayed_count_keeps_the_scale_words_its_reading_quoted_while_the_filing_states_them():
    # UnitedHealth: members in thousands, stated beside the table, not in it; replay dropped the quoted words, so every
    # replayed count was held for an unstated scale.
    spec = Spec.model_validate({"groups": [{"key": "people", "label": "People Served", "kind": "metric", "kpis": [
        {"key": "ma", "label": "Medicare Advantage Members", "unit": "count"}]}]})
    last = {"people.ma": {"table": "T0", "row": 1, "col": 1, "row_label": "Medicare Advantage", "header": "@ # #",
                          "scale": 1000, "scale_text": "(in thousands)"}}
    stated = parse_document(_members("7,900", " (in thousands)"), URL.format(n=2))
    values, problems = read_values(stated, spec, replay(stated, last, date(2026, 6, 30), 2), "USD")
    assert [(item.value, item.status) for item in values] == [(7_900_000, "verified")]
    # The words gone from the filing: the scale is not stated, and the figure is held, as before.
    unstated = parse_document(_members("7,900", ""), URL.format(n=2))
    values, _problems = read_values(unstated, spec, replay(unstated, last, date(2026, 6, 30), 2), "USD")
    assert [item.status for item in values] == ["needs_review"]


def test_a_row_the_filing_no_longer_has_is_left_out_and_the_rest_replay():
    # TSMC dropped its 10-nanometer row in 2024: every later report was sent to the AI for a row that no longer exists.
    ten = "<tr><td>10-nanometer</td><td>7</td><td>1</td></tr>"
    first = parse_document(report("March 31", 10, 20, 30, 40, 300, ten), URL.format(n=1))
    second = parse_document(report("June 30", 11, 25, 31, 42, 310), URL.format(n=2))
    table = next(iter(first.tables))
    located = ai.Located.model_validate({"period_end": "2026-03-31", "tables": [
        {"group": "technology", "table": table, "col": 1, "first_row": 2, "last_row": 6, "total_row": 7}]})
    items, problems = read_values(first, SPEC, located, "TWD")
    assert problems == [] and "r10nanometer" in {item.kpi.key for item in items}
    history = [{"group_key": f"kpi_{item.group.key}", "kpi_key": item.kpi.key, "fiscal_year": "2026", "fiscal_period": "Q1",
                "period_end": "2026-03-31", "method": "ai", "validation_status": "verified", "value": item.value,
                "locator": item.locator, "group_label": item.group.label, "kpi_label": item.kpi.label,
                "source_url": URL.format(n=1), "source_accession": "q1"} for item in items if not item.is_total]
    flash = {"period_end": "2026-06-30", "values": []}
    pipeline = _pipeline(NoAi({("locate", "flash"): flash, ("locate", "reasoning"): flash}), history)
    read, problems, _ = pipeline._read(lambda: "prompt", second, SPEC, Q2, version=1)
    assert pipeline.result.replay_outcomes.get("replayed") == 1, pipeline.result.replay_outcomes
    assert {item.kpi.key: item.value for item in read if not item.is_total}["r2nanometer"] == 11_000
    # Carried to another table of the filing, the row is not gone: a table read in part is never taken for a whole one.
    carried = report("June 30", 11, 25, 31, 42, 310) + "<table><tr><td>10-nanometer</td><td>7</td></tr></table>"
    assert not row_gone(parse_document(carried, URL.format(n=2)), {"row_label": "10-nanometer"})
    assert row_gone(second, {"row_label": "10-nanometer"})


def test_a_sentence_set_in_a_layout_table_is_replayed():
    # Visa's releases set their results in a one-column table of paragraphs: replay looked in passages only, so its
    # processed transactions went to the AI every quarter.
    before = ("Total processed transactions, which represent transactions processed by Visa, for the three months ended "
              "March 31, 2023, were 50.1 billion")
    document = parse_document("<html><body><p>Visa Inc. reports fiscal third quarter 2023 results</p><table><tr><td>Net revenues were $8.1 billion, an increase of 12%.</td></tr><tr><td>"
                              "Total processed transactions, which represent transactions processed by Visa, for the three "
                              "months ended June 30, 2023, were 54.0 billion, a 10% increase over the prior year.</td></tr></table></body></html>",
                              URL.format(n=2))
    found = replay(document, {"operating.members": {"block": "T0", "quote": before, "value_text": "50.1 billion"}}, Q2)
    assert not isinstance(found, str) and found.values[0].value_text == "54.0 billion" and found.values[0].block == "T0"


def test_a_sentence_whose_words_after_the_figure_changed_is_found_by_its_start():
    # KKR: "$316 billion, up 3% year-over-year" became "$321 billion, remained flat year-over-year".
    assert _replayed("Performance Fee Eligible AUM: $316 billion, up 3% year-over-year", "$316 billion",
                     "Performance Fee Eligible AUM: $321 billion, remained flat year-over-year") == "$321 billion"
    assert _replayed("there was $37 billion of committed capital with a weighted average management fee rate of ~100 bps",
                     "$37 billion", "At quarter end, there was $38 billion of committed capital with a weighted average "
                     "management fee rate of approximately 100 bps") == "$38 billion"
    # The quarter the figure is for stays in the start: "for the year" after the year's figure does not matter.
    assert _replayed("Capital Invested of $9 billion in the quarter and $44 billion in the LTM", "$9 billion",
                     "Capital Invested of $16 billion in the quarter and $44 billion for the year") == "$16 billion"


def test_a_shortened_sentence_never_drops_the_figures_period_or_lands_on_another_span():
    # The period word after the figure is never cut, so the LTM's figure is not read from the quarter's sentence.
    assert _replayed("The infrastructure portfolio appreciated 18% and real estate appreciated 3% in the LTM", "18%",
                     "The infrastructure portfolio appreciated 4% and real estate appreciated 1% in the quarter") \
        == "sentence not found"
    # A start with no period word is not taken where the new sentence goes on to name the full year.
    assert _replayed("Revenue was $5.2 billion, up 10% year-over-year", "$5.2 billion",
                     "Revenue was $21.0 billion for the full year, up 9% year-over-year") == "sentence not found"
    # Too few words to name the figure: "was $5 billion" alone could be any figure.
    assert _replayed("It was $5.2 billion, up 10% year-over-year", "$5.2 billion", "It was $6.0 billion, flat") \
        == "sentence not found"


def test_a_sentence_found_in_several_places_hands_back_each_place_with_its_section():
    # Mastercard's release gives the quarter's bullets, then the same bullets under "Year-to-date Results".
    document = parse_document("<html><body><p>Mastercard's second quarter 2023 results, versus the year ago period.</p>"
                              "<p>Cross-border volume growth of 24% on a local currency basis.</p><p>Year-to-date Results</p>"
                              "<p>Primary drivers were as follows: Cross-border volume growth of 29% on a local currency basis.</p>"
                              "</body></html>", URL.format(n=2))
    found = replay(document, {"operating.members": {"block": "P1", "quote": "Cross-border volume growth of 35% on a local "
                                                    "currency basis.", "value_text": "35%"}}, Q2)
    from kpi_extractor.readers.replay import AmbiguousQuote
    assert isinstance(found, AmbiguousQuote) and found == "sentence found more than once"
    assert [(locator.value_text, section) for locator, section in found.candidates] == [("24%", "quarter"), ("29%", "longer")]


def _choose(candidates, year_ago_rows, stored, prose=True):
    """The reading step's choice among places, with every check passing and each place's year-ago cells given."""
    from kpi_extractor.readers.replay import AmbiguousQuote
    from kpi_extractor.ai import Locator
    pipeline = _pipeline(NoAi(), [])
    places = [(Locator(kpi="operating.members", block=f"P{i}", quote=f"Growth of {value} in the period." if prose
                       else f"Ending Balance ${value} $1 ${value + 1}", value_text=str(value)), section)
              for i, (value, section) in enumerate(candidates)]
    def checked(document, part, located, expected, currency, mark=None, annual=False):
        item = SimpleNamespace(value=float(located.values[0].value_text), locator={"block": located.values[0].block})
        return located, [item]
    pipeline._checked = checked
    pipeline._stored_year_ago = lambda expected: {"operating.members": stored} if stored is not None else {}
    pipeline._year_ago_cell_matches = lambda item, document, before: before is not None and before in year_ago_rows[item.locator["block"]]
    result = pipeline._one_of(None, SPEC, "operating.members", AmbiguousQuote(places), Q2, "USD")
    return result if isinstance(result, str) else result[1][0].value


def test_one_of_several_places_is_kept_only_on_the_filings_own_evidence(monkeypatch):
    from kpi_extractor.steps import replay_reading
    monkeypatch.setattr(replay_reading, "reported_period", lambda document, expected: expected)
    # KKR: the firm's "Management Fees" row and each segment's; only the firm's year-ago column holds the stored figure.
    rows = {"P0": [654_927], "P1": [299_099], "P2": [161_707]}
    assert _choose([(748_888, None), (320_081, None), (201_590, None)], rows, 654_927) == 748_888
    # The same table again in the appendix: two places, one figure.
    assert _choose([(748_888, None), (748_888, None)], {"P0": [654_927], "P1": [654_927]}, 654_927) == 748_888
    # Mastercard: no year-ago column; the quarter's section is kept, the year to date's is not.
    assert _choose([(24, "quarter"), (29, "longer")], {"P0": [], "P1": []}, None) == 24
    # A table row repeated with one figure, no year-ago figure stored: the figure stands.
    assert _choose([(114_675, None), (114_675, None)], {"P0": [], "P1": []}, None, prose=False) == 114_675
    # Two different figures still standing: neither is taken.
    assert _choose([(24, "quarter"), (21, "quarter")], {"P0": [], "P1": []}, None) == "sentence found more than once"
    assert _choose([(24, None), (29, None)], {"P0": [], "P1": []}, None) == "sentence found more than once"
    assert _choose([(24, "quarter"), (29, None)], {"P0": [], "P1": []}, None) == "sentence found more than once"
    # Apollo: "Ending Balance" rows of two roll-forwards (total AUM, fee-generating AUM), with no year-ago column: the
    # quarter's section names no table, so the total AUM's quarter row is never taken for the fee-generating one.
    assert _choose([(1_073_599, "quarter"), (1_150_000, "longer")], {"P0": [], "P1": []}, 685_026, prose=False) \
        == "sentence found more than once"


def test_a_column_header_whose_title_or_unit_words_drifted_still_heads_the_same_column():
    from kpi_extractor.readers.replay import comparable_header, same_period_words
    same = lambda now, before, cut=False: same_period_words(comparable_header(now), comparable_header(before), cut)
    # Visa: the quarter's title row ("Q3 FISCAL 2023") one quarter, "USD" the next.
    assert same("income statement summary three months endedseptember # # usd (in millions# except percentages and per share data)",
                "<third quarter> fiscal # income statement summary three months endedjune # # (in millions# except percentages and per sh", cut=True)
    # Morgan Stanley: the table's title rows above "Quarter Ended".
    assert same("investment management financial information and statistical data (unaudited# dollars in billions) quarter ended @ # #",
                "financial information and statistical data quarter ended @ # #")
    # A change column, a nine-month column, a measure no header names, an added measure word: never the same column.
    assert not same("income statement summary <third quarter> % change", "income statement summary three months ended @ #")
    assert not same("income statement summary nine months ended @ #", "income statement summary three months ended @ #")
    assert not same("wealth management statistical data quarter ended @ #", "quarter ended @ #")
    assert not same("non-gaap income statement summary three months ended @ #", "income statement summary three months ended @ #")
    assert not same("adjusted income statement summary three months ended @ #", "income statement summary three months ended @ #")
    # No span named at all: only the exact comparison can say.
    assert not same("unit case volume", "unit case volume")


def test_a_footnote_mark_on_a_heading_word_is_no_change_of_column():
    # Coca-Cola's "Unit Case Volume³" (2024) heads the column "Unit Case Volume" did.
    table = Table("T0", "", [["Percent Change", "Unit Case Volume3"], ["Consolidated", "1"]])
    from kpi_extractor.readers.replay import _same_header
    assert _same_header(table, 1, 1, {"header": "unit case volume"})
    assert not _same_header(table, 1, 1, {"header": "price/mix"})


def _annual(year, n2, n3, n5, others, members, heading="Years Ended December 31", new_row=None):
    total = n2 + n3 + n5 + others + (new_row or 0)
    extra = f"<tr><td>A16</td><td>{new_row}</td><td>-</td></tr>" if new_row else ""
    return f"""
<p>Annual report for the years ended December 31, {year} (Amounts in Thousands of New Taiwan Dollars)</p>
<table>
  <tr><td></td><td>{heading}</td></tr>
  <tr><td>Resolution</td><td>{year}</td><td>{year - 1}</td></tr>
  {extra}
  <tr><td>2-nanometer</td><td>{n2}</td><td>1</td></tr>
  <tr><td>3-nanometer</td><td>{n3}</td><td>2</td></tr>
  <tr><td>5-nanometer</td><td>{n5}</td><td>3</td></tr>
  <tr><td>Others</td><td>{others}</td><td>4</td></tr>
  <tr><td>Wafer revenue</td><td>{total}</td><td>10</td></tr>
</table>
<p>Paid memberships reached {members} million at the end of the year, up 12% from a year ago.</p>
"""


def _records(document, located, fiscal_year, fiscal_period, period_end, accession):
    items, problems = read_values(document, SPEC, located, "TWD")
    assert problems == []
    return [{"group_key": f"kpi_{item.group.key}", "kpi_key": item.kpi.key, "fiscal_year": fiscal_year, "fiscal_period": fiscal_period,
             "period_end": period_end, "method": "ai", "validation_status": "verified", "value": item.value,
             "locator": item.locator, "group_label": item.group.label, "kpi_label": item.kpi.label,
             "source_url": document.source_url, "source_accession": accession} for item in items if not item.is_total]


def _year_and_quarter_history():
    """Last year's annual report read as the year, and this year's Q3 report read as the quarter."""
    last_year = parse_document(_annual(2025, 100, 200, 300, 400, 280), "https://www.sec.gov/Archives/a2025q4annualreport.htm")
    q3 = parse_document(report("September 30", 30, 60, 80, 100, 305), "https://www.sec.gov/Archives/a2026q3report.htm")
    def located(document, period_end, annual):
        table = next(iter(document.tables))
        block = next(key for key, item in document.blocks.items() if "Paid memberships" in item.text)
        members = "280 million" if annual else "305 million"
        return ai.Located.model_validate({"period_end": period_end, "annual": annual, "tables": [
            {"group": "technology", "table": table, "col": 1, "first_row": 2, "last_row": 5, "total_row": 6}],
            "values": [{"kpi": "operating.members", "block": block, "quote": f"Paid memberships reached {members}",
                        "value_text": members}]})
    return (_records(last_year, located(last_year, "2025-12-31", True), "2025", "FY", "2025-12-31", "fy25")
            + _records(q3, located(q3, "2026-09-30", False), "2026", "Q3", "2026-09-30", "q326"))


def test_a_years_report_is_read_again_from_last_years_as_the_year():
    # TSMC files its annual consolidated report each Q4 under a new name, its figures in "Years Ended" columns: Q3's
    # "three months ended" places are rightly refused there, and last year's report's places read it as the year.
    this_year = parse_document(_annual(2026, 110, 230, 320, 410, 300), "https://www.sec.gov/Archives/a2026q4consolidated.htm")
    control = NoAi()  # any AI call would fail: none is expected
    pipeline = _pipeline(control, _year_and_quarter_history())
    read, problems, located = pipeline._read(lambda: "prompt", this_year, SPEC, date(2026, 12, 31), version=1)
    assert control.calls == [] and problems == [] and located.annual
    values = {item.kpi.key: item.value for item in read if not item.is_total}
    assert values["n3"] == 230_000 and values["members"] == 300_000_000
    assert pipeline.result.replay_outcomes.get("replayed as the year") == 1


def test_a_q4_release_headed_as_the_quarter_is_never_read_as_the_year():
    # The same layout headed "Three Months Ended December 31": last year's year places do not serve, the AI reads it.
    release = parse_document(_annual(2026, 30, 62, 85, 101, 300, heading="Three Months Ended December 31"),
                             "https://www.sec.gov/Archives/a2026q4consolidated.htm")
    flash = {"period_end": "2026-12-31", "values": []}
    control = NoAi({("locate", "flash"): flash, ("locate", "reasoning"): flash})
    pipeline = _pipeline(control, _year_and_quarter_history())
    pipeline._read(lambda: "prompt", release, SPEC, date(2026, 12, 31), version=1)
    assert ("locate", "flash") in control.calls and "replayed as the year" not in pipeline.result.replay_outcomes


def test_a_row_above_the_old_first_row_is_read_only_when_the_rows_then_add_up():
    from kpi_extractor.readers.replay import _with_rows_above
    rows = [["", "Years Ended December 31"], ["Resolution", "2023", "2022"], ["3-nanometer", "108", "-"],
            ["5-nanometer", "629", "508"], ["7-nanometer", "357", "535"], ["Wafer revenue", "1,094", "1,043"]]
    assert _with_rows_above(Table("T0", "", rows), 3, 5, 1) == 2  # 108 + 629 + 357 = 1,094, never the "2023" row
    unrelated = rows[:2] + [["Memo: prior year", "99", "-"]] + rows[3:5] + [["Wafer revenue", "986", "1,043"]]
    assert _with_rows_above(Table("T0", "", unrelated), 3, 5, 1) == 3  # 629 + 357 = 986 already: nothing above joins


def test_a_years_new_row_stands_when_the_table_adds_up_to_its_total():
    # TSMC's 2023 technology table opens with a 3-nanometer row 2022's did not have: no place last year, no year-ago
    # figure, and the rows add up to the wafer revenue the report states only with it.
    this_year = parse_document(_annual(2026, 110, 230, 320, 410, 300, new_row=55), "https://www.sec.gov/Archives/a2026q4consolidated.htm")
    control = NoAi()
    pipeline = _pipeline(control, _year_and_quarter_history())
    read, problems, located = pipeline._read(lambda: "prompt", this_year, SPEC, date(2026, 12, 31), version=1)
    assert control.calls == [] and located.annual
    assert {item.kpi.key: item.value for item in read if not item.is_total}["a16"] == 55_000


def test_a_figure_repeats_only_a_recent_period_and_not_one_its_row_states_again():
    from kpi_extractor.ai import GroupSpec, KpiSpec
    from kpi_extractor.readers.extract import ReadValue
    # Goldman's $17 billion of long-term net inflows in Q2 2025 was held as a repeat of 2016's: only the 13 months a
    # release's columns can show count.
    rows = [{"group_key": "kpi_operating", "kpi_key": "members", "fiscal_year": year, "fiscal_period": "Q2",
             "period_end": end, "method": "ai", "validation_status": "verified", "value": 17.0, "locator": {}}
            for year, end in (("2016", "2016-06-30"), ("2025", "2025-06-30"))]
    pipeline = _pipeline(NoAi(), rows)
    assert pipeline._earlier_values(date(2026, 6, 30)) == {"operating.members": [17.0]}  # 2025's, not 2016's
    assert pipeline._earlier_values(date(2027, 9, 30)) == {}
    # JPMorgan's lending fees were 1,828 in 2Q24 and in 2Q23: the row states both, so this column is not 2Q23's.
    def table(year_ago):
        return parse_document(f"""<table><tr><td></td><td>Three Months Ended June 30,</td><td>Three Months Ended June 30,</td></tr>
            <tr><td></td><td>2024</td><td>2023</td></tr><tr><td>Lending and deposit-related fees</td><td>1,828</td><td>{year_ago}</td></tr>
            </table>""", URL.format(n=2))
    group = GroupSpec(key="fees", label="Fees", kind="revenue_breakdown", kpis=[KpiSpec(key="lending", label="Lending", unit="currency")])
    item = ReadValue(group=group, kpi=group.kpis[0], value=1_828.0, currency="USD", locator={"table": "T0", "row": 2, "col": 1})
    assert not pipeline._repeats(item, table("1,828"), [1_828.0])
    assert pipeline._repeats(item, table("1,700"), [1_828.0])  # the year-ago column says otherwise: held
    assert not pipeline._repeats(item, table("1,700"), [1_700.0])


def test_a_quoted_breakdowns_total_is_the_one_row_of_its_block_holding_the_parts_added_up():
    from kpi_extractor.ai import Locator
    from kpi_extractor.readers.replay import stated_total
    # KKR's insurance revenue: each line quoted as a row of the slide's table, the total row never stored.
    text = ("Net Premiums 626,429 935,794 1,100,053 6,972,316 Policy Fees 315,382 333,900 629,184 662,847 "
            "Total Insurance Revenues 941,811 1,269,694 1,729,237 7,635,163 Memo: Net Flows 1,269,694 4 5 6")
    document = parse_document(f"<p>{text}</p>", URL.format(n=2))
    block = next(iter(document.blocks))
    parts = [Locator(kpi="g.premiums", block=block, quote="Net Premiums 626,429 935,794 1,100,053 6,972,316", value_text="935,794"),
             Locator(kpi="g.fees", block=block, quote="Policy Fees 315,382 333,900 629,184 662,847", value_text="333,900")]
    # "Memo: Net Flows" holds the same figure in its own first place, not the parts' second: only the total's row counts.
    total = stated_total(document, parts, "g.total")
    assert total is not None and total.value_text == "1,269,694" and total.quote.startswith("Total Insurance Revenues")
    # Two rows holding the sum: no total.
    twice = parse_document(f"<p>{text} Revenues 1 1,269,694 3 4</p>", URL.format(n=2))
    assert stated_total(twice, [p.model_copy(update={"block": next(iter(twice.blocks))}) for p in parts], "g.total") is None


def test_a_slides_unlabeled_subtotal_right_after_the_parts_is_their_total():
    from kpi_extractor.ai import Locator
    from kpi_extractor.readers.replay import stated_total
    # KKR's income statement slide: the insurance lines, then their subtotal with no label, then "Total Revenues".
    text = ("Insurance Net Premiums 626,429 935,794 1,100,053 6,972,316 Policy Fees 315,382 333,900 629,184 662,847 "
            "941,811 1,269,694 1,729,237 7,635,163 Total Revenues $ 3,626,518 $ 4,171,910 $ 6,754,000 $ 13,828,648")
    document = parse_document(f"<p>{text}</p>", URL.format(n=2))
    block = next(iter(document.blocks))
    parts = [Locator(kpi="g.premiums", block=block, quote="Insurance Net Premiums 626,429 935,794 1,100,053 6,972,316", value_text="935,794"),
             Locator(kpi="g.fees", block=block, quote="Policy Fees 315,382 333,900 629,184 662,847", value_text="333,900")]
    total = stated_total(document, parts, "g.total")
    assert total is not None and total.value_text == "1,269,694" and total.quote == "941,811 1,269,694 1,729,237 7,635,163"


def test_a_quarter_after_a_year_report_replays_from_the_latest_quarters_places():
    # TSMC's Q1 reports: the newest reading of their kind is Q4's annual report, read as the year, whose places are
    # never replayed into a quarter; the Q3 report's "three months ended" places serve instead.
    year = [dict(row, fiscal_year="2025", fiscal_period="FY", period_end="2025-12-31") for row in _year_and_quarter_history()
            if row["fiscal_period"] == "FY"]
    # Each report under a name of its own, as TSMC's are: no earlier reading shares the Q1 report's kind.
    quarter = [dict(row, fiscal_year="2025", fiscal_period="Q3", period_end="2025-09-30",
                    source_url="https://www.sec.gov/Archives/tsmc2025q3financialstatements.htm")
               for row in _year_and_quarter_history() if row["fiscal_period"] == "Q3"]
    q1 = parse_document(report("March 31", 12, 26, 33, 44, 320), "https://www.sec.gov/Archives/a2026q1consolidatdfinan.htm")
    control = NoAi()  # no AI call is expected
    pipeline = _pipeline(control, year + quarter)
    read, problems, located = pipeline._read(lambda: "prompt", q1, SPEC, date(2026, 3, 31), version=1)
    assert control.calls == [] and not located.annual
    assert {item.kpi.key: item.value for item in read if not item.is_total}["n3"] == 26_000


def _mastercard(worldwide_first, worldwide_six):
    rows = "".join(f"<tr><td>{label}</td><td>{gdv}</td></tr>" for label, gdv in [
        ("APMEA", "$572"), ("Worldwide", worldwide_first), ("Mastercard Credit", ""), ("APMEA", "$300"), ("Worldwide", "1,079"),
        ("For the 6 Months Ended June 30, 2023", ""), ("APMEA", "$1,100"), ("Worldwide", worldwide_six)])
    return parse_document(f"""<html><body><table><tr><td></td><td>For the 3 Months Ended June 30, 2023</td></tr>
        <tr><td></td><td>GDV (Bil.)</td></tr><tr><td>All Mastercard Branded Programs</td><td></td></tr>{rows}</table></body></html>""",
        "https://www.sec.gov/Archives/ma06302023-exx991xearnings.htm")


def test_a_label_the_table_repeats_per_section_is_read_where_it_sat_last_quarter():
    # Mastercard repeats "Worldwide" under each card program, then again for six months: the row it sat in last quarter
    # (always its first, the three months' all programs) is read, while no row above it opens another period's section.
    from kpi_extractor.readers.replay import _replay_value
    document = _mastercard("2,267", "4,375")
    table = next(iter(document.tables.values()))
    first = next(i for i, row in enumerate(table.rows) if row[0] == "Worldwide")
    six = max(i for i, row in enumerate(table.rows) if row[0] == "Worldwide")
    header = R_column_header(table, first, 1)
    found = _replay_value(document, "operating.gdv", {"table": table.id, "row": first, "col": 1, "row_label": "Worldwide", "header": header})
    assert not isinstance(found, str) and found.row == first
    # Last quarter's place in the six months' section is never taken: a row above it opens another span.
    assert _replay_value(document, "operating.gdv", {"table": table.id, "row": six, "col": 1, "row_label": "Worldwide",
                                                     "header": header}) == "row label found more than once"
    # Nor a row where the label no longer sits.
    assert _replay_value(document, "operating.gdv", {"table": table.id, "row": first + 1, "col": 1, "row_label": "Worldwide",
                                                     "header": header}) == "row label found more than once"


def R_column_header(table, row, col):
    from kpi_extractor.readers.extract import column_header
    return column_header(table, row, col)


def test_a_column_under_a_table_wide_year_is_the_newest():
    # Mastercard's operating table names its period once, over its first column: its other columns carry no year of
    # their own. A table naming one year has no other period's column; one naming two still needs the column's own.
    from kpi_extractor.readers.replay import current_column
    single = Table("T0", "", [["", "For the 3 Months ended June 30, 2023", ""], ["", "GDV", "Purchase Volume"], ["Worldwide", "2,267", "1,839"]])
    assert current_column(single, 2, 2)
    two = Table("T0", "", [["", "June 30, 2023", "June 30, 2022"], ["", "GDV", "Purchase Volume"], ["Worldwide", "2,267", "1,839"]])
    assert current_column(two, 2, 1) and not current_column(two, 2, 2)


def test_a_kpi_the_newest_reading_lacks_is_looked_for_where_an_earlier_quarter_found_it():
    # JPMorgan's operating metrics: one quarter's reading left a KPI out (missed, or not reported that quarter), and
    # every quarter after had no place for it. Q1 found the memberships; Q2's reading has only the technology table.
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    q1 = _history(first)
    q2 = [dict(row, fiscal_period="Q2", period_end="2026-06-30") for row in q1 if row["group_key"] == "kpi_technology"]
    third = parse_document(report("September 30", 12, 26, 33, 44, 320), URL.format(n=3))
    control = NoAi()  # no AI call is expected
    pipeline = _pipeline(control, q1 + q2)
    read, problems, _ = pipeline._read(lambda: "prompt", third, SPEC, date(2026, 9, 30), version=1)
    assert control.calls == [] and {item.kpi.key: item.value for item in read if not item.is_total}["members"] == 320_000_000


def _uber(q4=False):
    """Uber's release tables: "Freight" under Gross Bookings, Revenue and Segment Operating Income (a Q4 release has a
    full-year table first)."""
    year = "<table><tr><td></td><td>Year Ended December 31,</td><td></td></tr><tr><td>(In millions)</td><td>2024</td><td>2025</td></tr><tr><td>Trips</td><td>11,273</td><td>13,567</td></tr></table>" if q4 else ""
    def table(title, freight, ago):
        return (f"<table><tr><td></td><td>Three Months Ended March 31,</td><td></td></tr>"
                f"<tr><td>(In millions)</td><td>2025</td><td>2026</td></tr><tr><td>{title}:</td><td></td><td></td></tr>"
                f"<tr><td>Freight</td><td>{ago}</td><td>{freight}</td></tr></table>")
    html = year + table("Gross Bookings", "1,334", "1,259") + table("Revenue", "1,337", "1,260") \
        + table("Segment Operating Income (Loss)", "(30)", "(25)")
    return parse_document(html, "https://www.sec.gov/x/uberq126earningspressrelea.htm")


def test_a_row_label_other_tables_give_other_figures_is_not_placed_by_the_table_number_alone():
    from kpi_extractor.readers.replay import AmbiguousQuote, chosen_by_number
    document = _uber()
    gross, revenue, _income = list(document.tables)
    # Q4's place, one table later: the revenue table now has that number.
    place = {"row_label": "Freight", "table": revenue, "row": 3, "col": 2, "header": "three months ended @ #"}
    alone = replay(document, {"operating.freight_gross_bookings": place}, date(2026, 3, 31))
    assert isinstance(alone, AmbiguousQuote) and alone == "row in several tables"
    assert [(locator.table, section) for locator, section in alone.candidates] == [(revenue, "last"), (gross, None), (_income, None)]
    # Within a group the table number still places it, marked for the year-ago check.
    other = {"row_label": "Freight", "table": _income, "row": 3, "col": 2, "header": "three months ended @ #"}
    replay(document, {"operating.freight_gross_bookings": place, "operating.freight_income": other}, date(2026, 3, 31))
    assert {(revenue, 3, 2), (_income, 3, 2)} <= chosen_by_number(document)


def test_a_row_label_every_table_gives_one_figure_is_the_filings_figure_wherever_it_sits():
    # Goldman's summary page and its detail table: "Advisory" $1,494 in both; last quarter's table number is neither.
    row = "<tr><td></td><td>Three Months Ended March 31,</td><td></td></tr><tr><td></td><td>2026</td><td>2025</td></tr><tr><td>Advisory</td><td>$1,494</td><td>$792</td></tr>"
    document = parse_document(f"<table>{row}</table><table>{row}</table>", "https://www.sec.gov/x.htm")
    located = replay(document, {"revenue.advisory": {"row_label": "Advisory", "table": "T9", "row": 2, "col": 1,
                                                    "header": "three months ended @ #"}}, date(2026, 3, 31))
    assert not isinstance(located, str) and located.values[0].table == next(iter(document.tables))


def test_one_of_several_tables_is_the_one_whose_year_ago_cell_holds_the_stored_figure(monkeypatch):
    from kpi_extractor.steps import replay_reading
    from kpi_extractor.readers.replay import AmbiguousQuote
    from kpi_extractor.ai import Locator
    monkeypatch.setattr(replay_reading, "reported_period", lambda document, expected: expected)
    pipeline = _pipeline(NoAi(), [])
    places = lambda: AmbiguousQuote([(Locator(kpi="operating.members", table="T1", row=3, col=2), "last"),
                                     (Locator(kpi="operating.members", table="T0", row=3, col=2), None)], "row in several tables")
    figures, year_ago = {"T0": 1_334, "T1": 1_337}, {"T0": 1_259, "T1": 1_260}
    def checked(document, part, located, expected, currency, mark=None, annual=False):
        table = located.values[0].table
        return located, [SimpleNamespace(value=figures[table], locator={"table": table})]
    pipeline._checked = checked
    pipeline._year_ago_cell_matches = lambda item, document, before: before == year_ago[item.locator["table"]]
    pipeline._year_ago_differs = lambda item, document, before: before is not None and before != year_ago[item.locator["table"]] \
        and item.locator["table"] in shows_year_ago
    shows_year_ago = {"T0", "T1"}
    def choose(stored):
        pipeline._stored_year_ago = lambda expected: {"operating.members": stored} if stored is not None else {}
        return pipeline._one_of(None, SPEC, "operating.members", places(), Q2, "USD")
    # Uber's Q1 2026: last year's bookings (1,259) pick the bookings table over last quarter's table number.
    assert choose(1_259)[1][0].value == 1_334
    # A stored figure no table's year-ago cell holds (restated since): neither is taken.
    assert choose(1_200) == "row in several tables"
    # Nothing stored to decide by: last quarter's table, as before.
    assert choose(None)[1][0].value == 1_337
    # Tables with no year-ago cell (Coca-Cola's changes): nothing contradicts last quarter's table, so it stands.
    shows_year_ago.clear()
    assert choose(1_200)[1][0].value == 1_337
