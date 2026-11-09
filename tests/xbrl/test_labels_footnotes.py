from kpi_extractor.xbrl.labels import without_footnote


def test_a_footnote_marker_run_into_a_label_is_dropped():
    assert without_footnote("Infrastructure(a)") == "Infrastructure"  # BAM's 2026 segments
    assert without_footnote("devices (1)") == "devices"
    assert without_footnote("Q4") == "Q4" and without_footnote("(a)") == "(a)"
    assert without_footnote("Data Center") == "Data Center"
