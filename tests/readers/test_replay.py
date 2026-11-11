import json
from datetime import date
from types import SimpleNamespace

from kpi_extractor import ai
from kpi_extractor.ai import Spec
from kpi_extractor.documents.document import parse_document
from kpi_extractor.readers.extract import read_values
from kpi_extractor.pipeline import SymbolPipeline
from kpi_extractor.readers.replay import replay, reported_period

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
    assert read["n3"] == 25_000 and "a16" not in read  # the listed 3nm KPI keeps its key  # the new row sits above the old first row


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
