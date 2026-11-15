import gzip
import json
from datetime import date

from statement_extractor.pipeline.served import available_from, served_snapshot
from statement_extractor.pipeline.store import evidence_name, store

LINE = {"line": 0, "concept": "us-gaap_Revenues", "key": "us-gaap_Revenues", "label": "Total net sales", "level": 0,
        "is_heading": False, "value": 391_035_000_000.0, "unit": "USD", "decimals": -6, "taxonomy": "us-gaap/2024",
        "negated": False, "code": "kept in the evidence only"}


def _record(**extra):
    return {"statement": "income", "fiscal_year": "2024", "fiscal_period": "FY", "duration": "year", "status": "kept",
            "period_end": "2024-09-28", "period_start": "2023-10-01", "basis": "US GAAP", "currency": "USD",
            "scope": "consolidated", "source": "xbrl", "source_accession": "0000320193-24-000123", "source_form": "10-K",
            "source_filed": "2024-11-01", "from_comparative": False, "own_accession": "0000320193-24-000123",
            "witness": "single", "checks": {"checks": [], "arelle": None}, "lines": [LINE], **extra}


def test_a_source_is_public_from_its_edgar_acceptance_else_the_end_of_its_filing_day():
    accepted = {"0000320193-24-000123": "2024-11-01T10:01:36+00:00"}
    assert available_from("0000320193-24-000123", "2024-11-01", accepted) == "2024-11-01T10:01:36+00:00"
    # A regulator's form: the end of its filing day in New York (EST in January, EDT in July).
    assert available_from("CVM-ITR-160183-v1", "2024-01-15", accepted) == "2024-01-16T04:59:59+00:00"
    assert available_from("CVM-ITR-160183-v1", "2024-07-15", accepted) == "2024-07-16T03:59:59+00:00"


def test_the_snapshot_serves_kept_periods_and_their_other_bases():
    other = {"basis": "IFRS", "source_accession": "0001-25-1", "source_filed": "2025-03-01", "source_form": "20-F",
             "from_comparative": True, "scope": "consolidated", "witness": "single", "source": "xbrl",
             "period_start": "2023-10-01", "checks": {"checks": [], "arelle": None}, "currency": "USD", "lines": [LINE]}
    chosen = [_record(other_bases=[other, {**other, "basis": "BR GAAP", "currency": None}]),
              _record(fiscal_year="2023", status="rejected")]
    filings = [{"accession": "0000320193-24-000123", "filed": "2024-11-01", "accepted": "2024-11-01T10:01:36+00:00"}]
    served = served_snapshot("AAPL", 320193, chosen, filings, [(date(2020, 8, 31), 4.0, "")], {"periods": 2})
    assert [(row["basis"], row["is_primary"], row["own_accession"]) for row in served["periods"]] == [
        ("US GAAP", True, None), ("IFRS", False, "0000320193-24-000123")]
    assert served["periods"][0]["available_from"] == "2024-11-01T10:01:36+00:00"
    assert "code" not in served["periods"][0]["lines"][0]
    assert served["splits"] == [{"first_filed": "2020-08-31", "ratio": 4.0, "share_class": ""}]
    assert served["read"]["last_filed"] == "2024-11-01" and served["read"]["coverage"]["periods"] == 2
    assert served["read"]["coverage"]["unserved"] == [{"statement": "income", "fiscal_year": "2024", "fiscal_period": "FY",
        "duration": "year", "basis": "BR GAAP", "source_accession": "0001-25-1", "why": "no currency stated"}]


def _folder(tmp_path):
    readings = [{"accession": "0000320193-24-000123", "statement": "income"},
                {"accession": "archive:TSM:20-F:2000-12-31", "statement": "balance"}]
    (tmp_path / "readings.jsonl").write_text("\n".join(json.dumps(reading) for reading in readings) + "\n")
    for name, value in (("filings", []), ("summary", {"symbol": "AAPL"}), ("served", {"cik": 320193, "periods": []})):
        (tmp_path / f"{name}.json").write_text(json.dumps(value))
    return tmp_path


def test_a_write_uploads_the_evidence_then_serves_the_snapshot(tmp_path):
    posted, written = [], {}

    def post(body):
        posted.append(body)
        if body["operation"] == "uploads":
            return {"links": {file["name"]: f"https://r2/{file['name']}" for file in body["files"]}}
        return {"new": 0}

    result = store(_folder(tmp_path), post=post, put=lambda link, body: written.setdefault(link, body))
    names = [file["name"] for file in posted[0]["files"]]
    assert names == ["readings/0000320193-24-000123.json.gz", "readings/archive-TSM-20-F-2000-12-31.json.gz",
                     "filings.json.gz", "summary.json.gz", "served.json.gz"]  # the snapshot last
    assert all(file["size"] == len(written[f"https://r2/{file['name']}"]) for file in posted[0]["files"])
    assert json.loads(gzip.decompress(written["https://r2/served.json.gz"])) == {"cik": 320193, "periods": []}
    assert posted[-1] == {"operation": "store", "cik": 320193, "dry_run": False, "allow_withdraw": False}
    assert result["files"] == 5 and evidence_name("archive:ASML:ARS:2000-12-31") == "readings/archive-ASML-ARS-2000-12-31.json.gz"


def test_a_dry_run_writes_only_its_candidate_snapshot(tmp_path):
    posted, written = [], {}

    def post(body):
        posted.append(body)
        if body["operation"] == "uploads":
            return {"links": {file["name"]: f"https://r2/{file['name']}" for file in body["files"]}}
        return {"dry_run": True}

    store(_folder(tmp_path), dry_run=True, post=post, put=lambda link, body: written.setdefault(link, body))
    assert [file["name"] for file in posted[0]["files"]] == ["candidate.json.gz"]
    assert list(written) == ["https://r2/candidate.json.gz"] and posted[-1]["dry_run"] is True
    # Gzipped without a time stamp: one snapshot is always the same bytes.
    store(_folder(tmp_path), dry_run=True, post=post, put=lambda link, body: written.__setitem__("again", body))
    assert written["again"] == written["https://r2/candidate.json.gz"]


def test_a_read_with_units_stores_them_and_the_next_read_gets_them_back(tmp_path):
    from statement_extractor.pipeline.store import fetch_units
    from statement_extractor.pipeline.units import Units
    (tmp_path / "read").mkdir()
    folder = _folder(tmp_path / "read")
    units = Units(folder)
    units.keep("report", "0000320193-24-000123", {"offset": 0}, {"accession": "0000320193-24-000123"}, [],
               accession="0000320193-24-000123", filed="2024-11-01")
    units.finish()
    posted, written = [], {}

    def post(body):
        posted.append(body)
        if body["operation"] == "uploads":
            return {"links": {file["name"]: f"https://r2/{file['name']}" for file in body["files"]}}
        if body["operation"] == "downloads":
            return {"links": {link[len("https://r2/"):]: link for link in written if "/units/" in link}}
        return {"new": 0}

    store(folder, post=post, put=lambda link, body: written.setdefault(link, body))
    names = [file["name"] for file in posted[0]["files"]]
    assert names == ["units/report-0000320193-24-000123.json.gz", "units/index.json.gz", "filings.json.gz",
                     "summary.json.gz", "served.json.gz"]  # units in place of readings/, the snapshot last
    later = tmp_path / "previous"
    assert fetch_units(later, 320193, post=post, get=lambda link: written[link]) == 1
    assert posted[-1] == {"operation": "downloads", "cik": 320193}
    again = Units(tmp_path / "next", previous=later)
    assert again.reuse("report-0000320193-24-000123", {"offset": 0})["record"] == {"accession": "0000320193-24-000123"}
