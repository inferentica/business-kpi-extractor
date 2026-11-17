

def test_a_lone_dash_where_the_figure_stood_is_none_not_the_next_column():
    # Apollo's 3Q'23 release: "Pension group annuities — 9,058" (the quarter none, the year to date 9,058). The anchor
    # rule read past the dash and served the year to date for the quarter (code-reads 2026-11-17: 3 wrong).
    from kpi_extractor.documents.document import Document, TextBlock
    from kpi_extractor.readers.anchor import anchor_locator
    doc = Document(source_url="https://www.sec.gov/r.htm")
    doc.blocks["A_P1"] = TextBlock(id="A_P1", text="($ in millions) 3Q'23 YTD'23 Retail $6,523 $21,883 Flow reinsurance 3,174 7,749 "
                                                    "Pension group annuities — 9,058 Gross organic inflows 12,942 43,583")
    doc.order.append("A_P1")
    last = {"quote": "Pension group annuities 9,002 9,058", "value_text": "9,002"}
    assert anchor_locator(doc, "flows.pga", last, "Pension Group Annuities Inflows") == "a dash stands where the figure was"
    found, _words = anchor_locator(doc, "flows.fr", {"quote": "Flow reinsurance 1,000 4,575", "value_text": "1,000"}, "Flow Reinsurance")
    assert found.value_text == "3,174"
    # A dash that stood there last quarter too is the sentence's own punctuation.
    doc.blocks["A_P1"].text = "Assets under management — $712 billion at quarter end"
    found, _words = anchor_locator(doc, "aum", {"quote": "Assets under management — $698 billion", "value_text": "$698 billion"},
                                   "Assets Under Management")
    assert found.value_text == "$712 billion"
