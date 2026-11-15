import json

from statement_extractor.quality.golden import GOLDEN, score


def chosen(folder, lines, period=("income", "2009", "FY", "year")):
    folder.mkdir()
    (folder / "chosen.json").write_text(json.dumps([{
        "statement": period[0], "fiscal_year": period[1], "fiscal_period": period[2], "duration": period[3],
        "status": "kept", "lines": lines}]))


def golden(path, entries):
    path.write_text(json.dumps({"entries": entries}))
    return path


def line(concept, label, value):
    return {"line": 0, "concept": concept, "label": label, "level": 0, "is_heading": False, "value": value}


def test_entries_score_correct_wrong_missing_and_absent(tmp_path):
    chosen(tmp_path / "AAPL", [line("us-gaap_SalesRevenueNet", "Net sales", 36_537e6),
                               line("us-gaap_NetIncomeLoss", "Net income", 5_704e6)])
    base = {"symbol": "AAPL", "statement": "income", "fiscal_year": "2009", "fiscal_period": "FY", "duration": "year",
            "scale": 1e6}
    path = golden(tmp_path / "golden.json", [
        {**base, "concept": "us-gaap_SalesRevenueNet", "label": "Net sales", "value": 36_537e6, "era": "xbrl"},
        {**base, "concept": "us-gaap_NetIncomeLoss", "label": "Net income", "value": -5_704e6, "era": "xbrl"},
        {**base, "label": "^gross margin$", "value": 13_140e6, "era": "html"},
        {**base, "label": "^net income$", "value": 5_704.4e6, "era": "html"},
        {**base, "concept": "jpm_Memo", "label": "Memo", "value": 1.0, "era": "xbrl", "absent": True},
    ])
    outcomes = [result["outcome"] for result in score([tmp_path / "AAPL"], path)["results"]]
    assert outcomes == ["correct", "wrong", "missing", "correct", "correct"]


def test_the_golden_set_is_well_formed():
    entries = json.loads(GOLDEN.read_text())["entries"]
    assert len(entries) >= 600
    eras = {entry["era"] for entry in entries}
    assert {"xbrl", "html", "text"} <= eras
    for entry in entries:
        assert entry["statement"] in ("income", "balance", "cash_flow")
        assert entry["duration"] in ("quarter", "ytd", "year", "instant")
        assert entry.get("absent") or isinstance(entry["value"], (int, float))


def test_an_entry_on_another_basis_is_checked_against_that_basis_presentation(tmp_path):
    # TSMC's H1 2021: served on IFRS from its 6-K XBRL, its Taiwan-IFRS releases kept beside it.
    folder = tmp_path / "TSM"
    folder.mkdir()
    period = {"statement": "income", "fiscal_year": "2021", "fiscal_period": "Q2", "duration": "ytd", "status": "kept",
              "basis": "IFRS", "lines": [line("ifrs-full_ProfitLoss", "Net income", 277_142.9e6)],
              "other_bases": [{"basis": "Taiwan-IFRS", "lines": [line("tifrs_NetIncome", "NET INCOME", 274_220.465e6)]}]}
    (folder / "chosen.json").write_text(json.dumps([period]))
    base = {"symbol": "TSM", "statement": "income", "fiscal_year": "2021", "fiscal_period": "Q2", "duration": "ytd",
            "scale": 1e6}
    path = golden(tmp_path / "golden.json", [
        {**base, "label": "^NET INCOME$", "value": 274_220.465e6, "era": "release", "basis": "Taiwan-IFRS"},
        {**base, "concept": "ifrs-full_ProfitLoss", "label": "Net income", "value": 277_143e6, "era": "xbrl",
         "basis": "IFRS"}])
    assert [result["outcome"] for result in score([folder], path)["results"]] == ["correct", "correct"]
