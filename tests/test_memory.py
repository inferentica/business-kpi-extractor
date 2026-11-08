import os
from types import SimpleNamespace

from kpi_extractor.ops.memory import MemoryWatch, available_gb, peak_gb, process_gb


def test_available_memory_is_read_and_a_mac_has_none():
    assert available_gb("MemTotal: 16000 kB\nMemAvailable:  2097152 kB\n") == 2.0
    assert available_gb("nothing useful") is None


def test_a_process_that_has_ended_holds_nothing():
    assert process_gb(os.getpid(), "1000 262144 0") > 0
    assert process_gb(99999999) == 0.0


def test_the_watch_keeps_the_lowest_and_logs_each_process(monkeypatch):
    readings = iter([5.0, 2.5, 4.0])
    monkeypatch.setattr("kpi_extractor.ops.memory.available_gb", lambda: next(readings))
    monkeypatch.setattr("kpi_extractor.ops.memory.process_gb", lambda pid: {1: 1.0, 2: 3.0}[pid])
    lines = []
    watch = MemoryWatch(SimpleNamespace(_processes={1: None, 2: None}), log=lines.append)
    for _ in range(3):
        watch.sample()
    assert watch.lowest_available_gb == 2.5
    assert lines[0] == "Memory: 5.0 GB available; company processes hold 3.0, 1.0 GB"


def test_the_peak_is_a_positive_figure():
    assert peak_gb() > 0
