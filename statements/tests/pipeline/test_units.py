"""Read units (pipeline/units.py): a unit is reused only in the same context with the same reader, comes back as the
same readings, and the newest filings' units can be set aside to prove an incremental read equals a full one."""
import json
from datetime import date

import pytest

from statement_extractor.domain.model import Check, Reading
from statement_extractor.pipeline import units as units_module
from statement_extractor.pipeline.units import Units, readings_of, record_of, unit_id


def _reading(accession="0000320193-26-000026", filed=date(2026, 11, 3)) -> Reading:
    return Reading(statement="income", duration="year", fiscal_year="2026", fiscal_period="FY",
                   period_end=date(2026, 9, 26), period_start=date(2025, 9, 28), currency=None, accession=accession,
                   form="10-K", filed=filed, own=True, source="html", basis="US GAAP",
                   lines=[{"line": 1, "concept": "printed_net_sales", "label": "Net sales", "level": 0,
                           "is_heading": False, "value": 416161000000.0}],
                   checks=[Check("calc", "pass", "")], titled="Apple Inc.", release_of="", template=False,
                   audited_of="", arelle=None)


def test_a_reading_comes_back_with_every_field():
    reading = _reading()
    again = Reading.from_raw(json.loads(json.dumps(reading.to_raw())))
    assert again == reading
    assert again.titled == "Apple Inc." and again.status == "kept"


def test_a_unit_is_reused_in_the_same_context_only(tmp_path):
    first = Units(tmp_path / "one")
    unit = first.keep("report", "0000320193-26-000026", {"fiscal_year_end": "0927", "offset": 0}, {"accession": "a"},
                      [_reading()], facts={"period_end": "2026-09-26"}, accession="0000320193-26-000026",
                      filed="2026-11-03")
    first.finish()
    assert readings_of(unit) == [_reading()] and record_of(unit) == {"accession": "a"}

    second = Units(tmp_path / "two", previous=tmp_path / "one")
    id = unit_id("report", "0000320193-26-000026")
    assert second.stored_extra(id, "period_end") == "2026-09-26"
    assert second.reuse(id, {"fiscal_year_end": "0928", "offset": 0}) is None  # another year end: read again
    again = second.reuse(id, {"offset": 0, "fiscal_year_end": "0927"})  # key order does not matter
    assert readings_of(again) == [_reading()]
    assert second.finish()["reused"] == 1 and second.counts["read again"] == 1
    # The reused unit is written again, so the next read has it too.
    assert (tmp_path / "two" / "units" / f"{id}.json.gz").exists()


def test_another_reader_version_reads_again(tmp_path, monkeypatch):
    first = Units(tmp_path / "one")
    first.keep("release", "0001", {"offset": 0}, {}, [_reading("0001")], accession="0001", filed="2026-01-01")
    first.finish()
    monkeypatch.setattr(units_module, "reader_version", lambda: "another")
    second = Units(tmp_path / "two", previous=tmp_path / "one")
    assert second.reuse(unit_id("release", "0001"), {"offset": 0}) is None


def test_an_incomplete_unit_is_read_again(tmp_path):
    first = Units(tmp_path / "one")
    first.keep("report", "0001", {"offset": 0}, {"note": "unreadable"}, [], accession="0001", filed="2026-01-01",
               complete=False)
    first.finish()
    assert Units(tmp_path / "two", previous=tmp_path / "one").reuse(unit_id("report", "0001"), {"offset": 0}) is None


def test_the_newest_filings_can_be_set_aside(tmp_path):
    first = Units(tmp_path / "one")
    for accession, filed in (("0001", "2026-01-01"), ("0002", "2026-04-01"), ("0003", "2026-07-01")):
        first.keep("report", accession, {}, {}, [], accession=accession, filed=filed)
    first.keep("second", "0003", {}, None, [], accession="0003", filed="2026-07-01")
    first.finish()
    second = Units(tmp_path / "two", previous=tmp_path / "one", drop_newest=1)
    assert second.reuse(unit_id("report", "0003"), {}) is None
    assert second.reuse(unit_id("second", "0003"), {}) is None  # its second look goes with it
    assert second.reuse(unit_id("report", "0002"), {}) is not None
    assert second.stored_extra(unit_id("report", "0003"), "period_end") is None


def test_a_unit_holds_only_json_values(tmp_path):
    with pytest.raises(TypeError):
        Units(tmp_path).keep("report", "0001", {"when": date(2026, 1, 1)}, {}, [])


def test_code_left_out_of_the_reader_version_is_not_on_the_read_path():
    import subprocess
    import sys
    script = ("import sys; import statement_extractor.pipeline.company; "
              "print('\\n'.join(name for name in sys.modules if name.startswith('statement_extractor')))")
    loaded = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True).stdout.split()
    for path in units_module.AFTER_READING:
        module = "statement_extractor." + path.removesuffix(".py").removesuffix("/").replace("/", ".")
        if path == "pipeline/served.py":
            continue  # imported by the pipeline, run only once every unit is kept
        assert module not in loaded and not any(name.startswith(module + ".") for name in loaded), module


def test_a_change_after_reading_keeps_the_reader_version(tmp_path, monkeypatch):
    import shutil
    copy = tmp_path / "statement_extractor"
    shutil.copytree(units_module.PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(units_module, "PACKAGE", copy)
    units_module.reader_version.cache_clear()
    before = units_module.reader_version()
    (copy / "pipeline" / "store.py").write_text("# changed\n")
    units_module.reader_version.cache_clear()
    assert units_module.reader_version() == before
    (copy / "pipeline" / "reports.py").write_text("# changed\n")
    units_module.reader_version.cache_clear()
    assert units_module.reader_version() != before
    units_module.reader_version.cache_clear()
