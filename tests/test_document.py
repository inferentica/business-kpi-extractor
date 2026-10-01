import pytest

from kpi_extractor.document import parse_document, parse_number

RELEASE = """
<html><body>
<p>Second Quarter 2026 Operational Highlights</p>
<p>Family daily active people (DAP) – DAP was 3.60 billion on average for June 2026, an increase of 3% year-over-year.</p>
<p>Segment results (in millions)</p>
<table>
  <tr><td></td><td colspan="4">Three Months Ended June 30,</td></tr>
  <tr><td></td><td colspan="2">2026</td><td colspan="2">2025</td></tr>
  <tr><td>Family of Apps</td><td>$</td><td>60,370</td><td>$</td><td>47,145</td></tr>
  <tr><td>Reality Labs</td><td>$</td><td>431</td><td>$</td><td>370</td></tr>
  <tr><td>Operating loss</td><td></td><td>(4,970</td><td>)</td><td>(4,530</td><td>)</td></tr>
  <tr><td>Share of revenue</td><td></td><td>99.3</td><td>%</td><td>99.2</td><td>%</td></tr>
</table>
<table><tr><td>Layout only</td></tr></table>
<p>Headcount was 75,472 as of June 30, 2026.</p>
</body></html>
"""


def test_tables_fold_split_symbols_and_keep_columns():
    document = parse_document(RELEASE, "https://example.com/ex99.htm", prefix="A_")
    assert list(document.tables) == ["A_T0"]
    table = document.tables["A_T0"]
    row = next(row for row in table.rows if row[0] == "Family of Apps")
    assert row[1:] == ["$60,370", "$47,145"]
    loss = next(row for row in table.rows if row[0] == "Operating loss")
    assert loss[1:] == ["(4,970)", "(4,530)"]
    share = next(row for row in table.rows if row[0] == "Share of revenue")
    assert share[1:] == ["99.3%", "99.2%"]
    assert table.declared_scale() == 1_000_000
    assert "Segment results" in table.context


def test_text_blocks_keep_document_order_around_tables():
    document = parse_document(RELEASE, "https://example.com/ex99.htm")
    assert document.order.index("T0") > 0
    assert any("3.60 billion" in block.text for block in document.blocks.values())
    assert any("Headcount was 75,472" in block.text for block in document.blocks.values())
    rendered = document.render()
    assert "[T0]" in rendered and "c1='$60,370'" in rendered


@pytest.mark.parametrize(("text", "value", "percent", "scale"), [
    ("$60,370", 60370, False, None),
    ("(4,970)", -4970, False, None),
    ("99.3%", 99.3, True, None),
    ("3.60 billion", 3.6e9, False, "billion"),
    ("NT$1,270.38 billion", 1.27038e12, False, "billion"),
    ("75,472", 75472, False, None),
    ("-2.5%", -2.5, True, None),
    ("$1.2B", 1.2e9, False, "billion"),
    ("30 months", 30, False, None),
])
def test_parse_number(text, value, percent, scale):
    parsed = parse_number(text)
    assert parsed.value == pytest.approx(value)
    assert parsed.percent is percent
    assert parsed.scale_word == scale


def test_parse_number_without_digits():
    assert parse_number("—") is None
    assert parse_number(None) is None


def test_large_documents_reach_the_ai_as_their_revenue_sections():
    filler = "".join(f"<p>Note {index}: lease terms and other matters unrelated to the business mix.</p>" for index in range(400))
    html = ("<p>Quarterly report for the three months ended June 30, 2026</p>" + filler
            + "<p>Disaggregation of revenue</p><table><tr><td>Platform</td><td>2026</td></tr>"
            + "<tr><td>High Performance Computing</td><td>830,369</td></tr></table>" + filler)
    document = parse_document(html, "https://www.sec.gov/x.htm")
    focused = document.render_for_prompt(4_000)
    assert len(focused) <= 4_000
    assert "High Performance Computing" in focused and "three months ended June 30, 2026" in focused
    assert len(focused) < len(document.render()) / 5


def test_right_aligned_amounts_in_cells_of_different_widths_share_one_column():
    from kpi_extractor.document import parse_document
    document = parse_document("""<table>
      <tr><td colspan="3">Resolution</td><td colspan="3"></td><td colspan="12">2025</td><td colspan="3"></td><td colspan="12">2024</td></tr>
      <tr><td colspan="3">3-nanometer</td><td colspan="3"></td><td colspan="6">$</td><td colspan="3">160,180,187</td>
          <td colspan="6"></td><td colspan="6">$</td><td colspan="3">45,448,960</td></tr>
      <tr><td colspan="3">5-nanometer</td><td colspan="6"></td><td colspan="6">254,408,255</td><td colspan="9"></td><td colspan="6">190,695,754</td></tr>
      <tr><td colspan="3">Wafer revenue</td><td colspan="3"></td><td colspan="6">$</td><td colspan="3">714,028,927</td>
          <td colspan="6"></td><td colspan="6">$</td><td colspan="3">521,896,971</td></tr>
    </table>""", "https://www.sec.gov/x.htm")
    table = next(iter(document.tables.values()))
    assert [row[1] for row in table.rows] == ["2025", "$160,180,187", "254,408,255", "$714,028,927"]
    assert [row[2] for row in table.rows] == ["2024", "$45,448,960", "190,695,754", "$521,896,971"]


def test_a_table_that_arrives_as_text_is_read_as_a_table():
    from kpi_extractor.document import parse_document
    text = ("<p>Three months ended Six months ended Jun 29, Jun 28, Jun 29, Jun 28, (Unaudited, €, in millions, except per "
            "share data) 2025 2026 2025 2026 Net system sales 5,596.1 6,564.8 11,336.5 12,844.2 Net service and field "
            "option sales 2,095.6 2,761.7 4,096.7 5,249.2 Total net sales 7,691.7 9,326.5 15,433.2 18,093.4 Total cost of "
            "sales (3,562.2) (4,291.1) (7,124.0) (8,413.0)</p>")
    document = parse_document(text, "https://www.sec.gov/x.htm")
    table = next(iter(document.tables.values()))
    assert table.rows[1][1:] == ["2025", "2026", "2025", "2026"]
    assert table.rows[0][2] == "Three months ended" and table.rows[0][3] == "Six months ended"
    assert table.rows[2] == ["Net system sales", "5,596.1", "6,564.8", "11,336.5", "12,844.2"]
    assert table.declared_scale() == 1e6
    margins = parse_document("<p>(Unaudited) 2025 2026 Gross margin 53.7 % 54.0 % Operating margin 34.6 % 37.1 % "
                             "Net margin 29.8 % 31.3 %</p>", "https://www.sec.gov/x.htm")
    assert next(iter(margins.tables.values())).rows[2] == ["Gross margin", "53.7%", "54.0%"]


def test_statements_that_arrive_as_dot_leader_text_become_tables():
    text = ("<p>REVENUES BY BUSINESS (in millions; unaudited) Alpha Beta Total Three Months Ended June 30, 2026 "
            "Total revenues ........ $86,017 $23,472 $109,489 Restructuring and other (2) ..... — (1) (1) "
            "Three Months Ended June 30, 2025 Total revenues ........ $86,103 $24,725 $110,828 "
            "Customer Revenues (in millions; unaudited) Three Months Ended June 30, Six Months Ended June 30, "
            "2026 2025 2026 2025 Domestic ....... 19,048 18,950 38,254 38,016 Global ....... 944 819 1,856 1,601 "
            "Total ....... 19,992 19,769 40,110 39,617</p>")
    document = parse_document(text, "https://www.sec.gov/x.htm")
    first, second = (document.tables[key] for key in document.tables)
    assert first.rows[0] == ["Three Months Ended June 30, 2026"]
    assert first.rows[1] == ["Total revenues", "86,017", "23,472", "109,489"]
    assert first.rows[3] == ["Three Months Ended June 30, 2025"] and first.declared_scale() == 1e6
    assert second.rows[1] == ["", "2026", "2025", "2026", "2025"] and second.rows[2][0] == "Domestic"
    assert "86,017" not in next(iter(document.blocks.values())).text
