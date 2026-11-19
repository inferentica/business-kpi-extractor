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
    # No acceptance time and no regulator named: the end of its filing day in New York (EST in January, EDT in July).
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


def test_a_parents_own_statement_is_served_but_never_primary():
    # TSMC's 2005 quarters: the parent company's statements where no group statement was read (decision 2026-11-19).
    filings = [{"accession": "0000320193-24-000123", "filed": "2024-11-01", "accepted": "2024-11-01T10:01:36+00:00"}]
    served = served_snapshot("TSM", 1046179, [_record(scope="parent")], filings, [], {"periods": 1})
    assert [(row["scope"], row["is_primary"]) for row in served["periods"]] == [("parent", False)]


def _folder(tmp_path):
    readings = [{"accession": "0000320193-24-000123", "statement": "income"},
                {"accession": "archive:TSM:20-F:2000-12-31", "statement": "balance"}]
    (tmp_path / "readings.jsonl").write_text("\n".join(json.dumps(reading) for reading in readings) + "\n")
    for name, value in (("filings", []), ("summary", {"symbol": "AAPL"}), ("served", {"cik": 320193, "periods": []})):
        (tmp_path / f"{name}.json").write_text(json.dumps(value))
    return tmp_path


def _uploads(posted):
    return [[file["name"] for file in body["files"]] for body in posted if body["operation"] == "uploads"]


def _stored_units(tmp_path, count):
    from statement_extractor.pipeline.units import Units
    (tmp_path / "read").mkdir()
    folder = _folder(tmp_path / "read")
    units = Units(folder)
    for number in range(count):
        units.keep("report", f"000{number}", {"offset": 0}, {}, [], accession=f"000{number}", filed="2024-11-01")
    units.finish()
    return folder


def test_a_failed_unit_upload_writes_no_index_or_snapshot(tmp_path):
    import pytest
    folder = _stored_units(tmp_path, 40)

    def post(body):
        if body["operation"] == "uploads":
            return {"links": {file["name"]: f"https://r2/{file['name']}" for file in body["files"]}}
        raise AssertionError("stored although an upload failed")

    written = {}

    def put(link, body):
        if link.endswith("report-00017.json.gz"):
            raise RuntimeError("evidence upload failed")
        written[link] = body

    with pytest.raises(RuntimeError):
        store(folder, post=post, put=put)
    assert "https://r2/units/index.json.gz" not in written and "https://r2/served.json.gz" not in written


def test_a_failed_unit_download_leaves_no_index_so_the_read_is_a_full_one(tmp_path):
    import pytest
    from statement_extractor.pipeline.store import fetch_units
    from statement_extractor.pipeline.units import Units
    links = {f"units/report-000{number}.json.gz": f"https://r2/{number}" for number in range(40)}
    links["units/index.json.gz"] = "https://r2/index"

    def get(link):
        if link == "https://r2/17":
            raise RuntimeError("unit download failed")
        return gzip.compress(b"[]") if link == "https://r2/index" else b"unit"

    with pytest.raises(RuntimeError):
        fetch_units(tmp_path / "previous", 1, post=lambda body: {"links": links}, get=get)
    assert not (tmp_path / "previous" / "units" / "index.json").exists()
    assert Units(tmp_path / "next", previous=tmp_path / "previous").reuse("report-0001", {"offset": 0}) is None


def test_a_write_uploads_the_evidence_then_serves_the_snapshot(tmp_path):
    posted, written = [], {}

    def post(body):
        posted.append(body)
        if body["operation"] == "uploads":
            return {"links": {file["name"]: f"https://r2/{file['name']}" for file in body["files"]}}
        return {"new": 0}

    result = store(_folder(tmp_path), post=post, put=lambda link, body: written.setdefault(link, body))
    assert _uploads(posted) == [["filings.json.gz", "readings/0000320193-24-000123.json.gz",
                                 "readings/archive-TSM-20-F-2000-12-31.json.gz", "summary.json.gz"],
                                ["served.json.gz"]]  # the snapshot last, once the rest is written
    assert all(file["size"] == len(written[f"https://r2/{file['name']}"])
               for body in posted if body["operation"] == "uploads" for file in body["files"])
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
    # Units in place of readings/; their index once they are written, then the snapshot.
    assert _uploads(posted) == [["filings.json.gz", "summary.json.gz", "units/report-0000320193-24-000123.json.gz"],
                                ["units/index.json.gz"], ["served.json.gz"]]
    later = tmp_path / "previous"
    assert fetch_units(later, 320193, post=post, get=lambda link: written[link]) == 1
    assert posted[-1] == {"operation": "downloads", "cik": 320193}
    again = Units(tmp_path / "next", previous=later)
    assert again.reuse("report-0000320193-24-000123", {"offset": 0})["record"] == {"accession": "0000320193-24-000123"}


def test_a_stamp_without_a_time_or_long_after_the_filing_day_is_no_publication():
    from statement_extractor.pipeline.served import available_from
    # Salesforce's 10-Q filed as of June 1, 2020, under an accession accepted February 24, 2022: its filing day.
    late = {"0001108524-22-000007": "2022-02-24T21:11:16+00:00"}
    assert available_from("0001108524-22-000007", "2020-06-01", late) == "2020-06-02T03:59:59+00:00"
    # Before April 2002 EDGAR stamps midnight New York: a date, its day's end.
    old = {"0000320193-97-000016": "1997-12-05T05:00:00+00:00"}
    assert available_from("0000320193-97-000016", "1997-12-05", old) == "1997-12-06T04:59:59+00:00"
    # An after-hours filing stamped the evening before its filing date is public at its stamp.
    evening = {"x": "2024-02-01T22:15:00+00:00"}
    assert available_from("x", "2024-02-02", evening) == "2024-02-01T22:15:00+00:00"


def test_a_regulators_form_is_public_on_its_own_markets_day():
    from statement_extractor.pipeline.served import available_from
    assert available_from("BMV-1", "2024-07-25", {}, "bmv") == "2024-07-26T05:59:59+00:00"  # Mexico City, UTC-6
    assert available_from("CVM-ITR-1", "2024-07-15", {}, "cvm") == "2024-07-16T02:59:59+00:00"  # São Paulo, UTC-3
    assert available_from("DART-1", "2024-08-14", {}, "dart") == "2024-08-14T14:59:59+00:00"  # Seoul, UTC+9
