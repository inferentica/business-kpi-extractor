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
