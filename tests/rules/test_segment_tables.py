"""A company-wide metric is never read from a table headed for one segment."""
from kpi_extractor.documents.document import Table
from kpi_extractor.rules.segment_tables import company_segments, names_segment, segment_names, segment_of

BNY = segment_names(["Investment and Wealth Management", "Investment Management", "Investment Services",
                     "Market and Wealth Services", "Other", "Securities Services"])
SEGMENT_PAGE = Table("B_T9", "", [["THE BANK OF NEW YORK MELLON CORPORATION", "", ""],
                                  ["SECURITIES SERVICES BUSINESS SEGMENT", "", ""], ["", "", ""],
                                  ["(dollars in millions, unless otherwise noted)", "3Q24", "2Q24"],
                                  ["Selected balance sheet data:", "", ""], ["Average loans", "$11,077", "$11,103"]])
COMPANY_PAGE = Table("B_T2", "", [["", "", "THE BANK OF NEW YORK MELLON CORPORATION"], ["CONSOLIDATED FINANCIAL HIGHLIGHTS", "", ""],
                                  ["", "3Q24", "2Q24"], ["Average loans", "$69,205", "$69,211"]])


def test_bnys_securities_services_page_is_the_segments_and_its_company_page_is_not():
    assert segment_of(SEGMENT_PAGE, BNY) == "securities services"
    assert segment_of(COMPANY_PAGE, BNY) is None
    # The company's average loans are not the segment's; a metric labelled for the segment is.
    assert not names_segment("securities services", "Average loans", "Operating metrics")
    assert names_segment("securities services", "Securities Services average loans", "")


def test_a_table_of_every_segment_side_by_side_is_no_one_segments():
    table = Table("T4", "", [["Revenue by business segment", "", ""], ["", "Securities Services", "Market and Wealth Services"],
                             ["Total revenue", "2,400", "1,300"]])
    assert segment_of(table, BNY) is None


def test_a_longer_segment_name_counts_over_the_shorter_one_it_holds():
    table = Table("T5", "", [["INVESTMENT AND WEALTH MANAGEMENT BUSINESS", ""], ["", "3Q24"], ["Average loans", "$12,900"]])
    assert segment_of(table, BNY) == "investment and wealth management"


def test_a_heading_saying_segment_counts_even_when_the_tag_lists_no_segments():
    table = Table("T6", "", [["Wealth Management segment", ""], ["", "3Q24"], ["Client assets", "$2,100"]])
    assert segment_of(table, []) == "wealth management"
    assert company_segments([{"group_key": "segments", "method": "xbrl", "kpi_label": "Securities Services"},
                             {"group_key": "segments", "method": "xbrl", "kpi_label": "Other"},
                             {"group_key": "kpi_operating", "method": "ai", "kpi_label": "Average loans"}]) == ["securities services"]


def test_a_segment_page_holds_a_label_only_where_the_company_prints_it_too():
    from kpi_extractor.documents.document import Document
    from kpi_extractor.rules.segment_tables import segment_page_of
    document = Document(source_url="x")
    document.tables = {"B_T2": COMPANY_PAGE, "B_T9": SEGMENT_PAGE}
    assert segment_page_of(document, SEGMENT_PAGE, "Average loans", BNY) == "securities services"
    assert segment_page_of(document, COMPANY_PAGE, "Average loans", BNY) is None
    # A metric printed on its segment's page alone (JPMorgan's active mobile customers) is read there.
    assert segment_page_of(document, SEGMENT_PAGE, "Market value of securities on loan", BNY) is None
    # The segment's figure printed on the company's row too is the company's.
    assert segment_page_of(document, SEGMENT_PAGE, "Average loans", BNY, 11077e6 / 1e6) == "securities services"
    assert segment_page_of(document, SEGMENT_PAGE, "Average loans", BNY, 69205) is None


def test_a_one_word_segment_heads_no_table_unless_the_heading_says_segment():
    names = segment_names(["KKR"])
    assert segment_of(Table("T1", "", [["KKR & CO. INC.", ""], ["", "3Q24"], ["Capital invested", "$32"]]), names) is None


def test_the_heading_line_above_a_table_counts():
    table = Table("A_T11", "(b) Including dividend-equivalents on share-based awards. Note: Throughout this document, sequential "
                  "growth rates are unannualized. SECURITIES SERVICES BUSINESS SEGMENT HIGHLIGHTS",
                  [["(dollars in millions, unless otherwise noted)", "", ""], ["3Q24", "", "2Q24"], ["Average loans", "$11,077", "$11,103"]])
    assert segment_of(table, BNY) == "securities services"
    company = Table("A_T7", "Total payout ratio of 103% year-to-date CONSOLIDATED FINANCIAL HIGHLIGHTS", COMPANY_PAGE.rows)
    assert segment_of(company, BNY) is None


def test_a_table_by_segment_and_a_sentence_naming_one_head_no_segments_table():
    uber = Table("T7", "", [["Results by offering and segment", ""], ["", "Q3 2025"], ["Gross Bookings", "$23,000"]])
    assert segment_of(uber, []) is None
    unh = Table("T8", "Adjusted scripts grew on new clients at Optum Rx", [["", "2026"], ["Medicaid", "7,200"]])
    assert segment_of(unh, segment_names(["Optum Rx", "UnitedHealthcare"])) is None


def test_labels_differing_only_by_footnotes_are_one_label():
    from kpi_extractor.documents.document import Document
    from kpi_extractor.rules.segment_tables import segment_page_of
    segment = Table("B_T9", "", [["SECURITIES SERVICES BUSINESS SEGMENT", "", ""], ["", "3Q24", "2Q24"],
                                 ["AUC/A at period end (in trillions) (b)", "$37.5", "$35.7"]])
    company = Table("A_T7", "", [["CONSOLIDATED FINANCIAL HIGHLIGHTS", "", ""], ["", "3Q24", "2Q24"],
                                 ["AUC/A at period end (in trillions) (current period is preliminary)", "$52.1", "$49.5"]])
    document = Document(source_url="x")
    document.tables = {"A_T7": company, "B_T9": segment}
    assert segment_page_of(document, segment, "AUC/A at period end (in trillions) (b)", BNY, 37.5) == "securities services"
    assert segment_page_of(document, segment, "AUC/A at period end (in trillions) (b)", BNY, 52.1) is None


def test_a_companys_segment_results_table_is_no_one_segments():
    table = Table("A_T9", "", [["KKR SEGMENT RESULTS AND OTHER SELECTED FINANCIAL INFORMATION (UNAUDITED)", ""],
                               ["", "Quarter Ended"], ["Assets Under Management", "191,265,400"]])
    assert segment_of(table, segment_names(["Asset Management", "Insurance", "Capital Markets"])) is None


def test_a_heading_joining_the_company_and_its_segments_is_no_one_segments():
    # The re-check's probe: "Consolidated and Segment Results" took "consolidated and" for a segment's name.
    for title in ("CONSOLIDATED AND SEGMENT RESULTS", "Total Company and Segment Data", "Firmwide and segment highlights"):
        table = Table("A_T3", "", [[title, ""], ["", "3Q24"], ["Average loans", "$69,205"]])
        assert segment_of(table, BNY) is None
    # A heading naming the one segment before the word still is that segment's.
    wealth = Table("A_T4", "", [["PRIVATE WEALTH SEGMENT", ""], ["", "3Q24"], ["Average loans", "$9,100"]])
    assert segment_of(wealth, BNY) == "private wealth"


def test_a_segment_title_after_the_pages_running_head_names_the_segment():
    # Wells Fargo's supplement: each page opens "Wells Fargo & Company and Subsidiaries", then its title in capitals.
    names = segment_names(["Commercial Banking", "Wealth and Investment Management", "Consumer Banking and Lending"])
    page = Table("B_T14", "Wells Fargo & Company and Subsidiaries WEALTH AND INVESTMENT MANAGEMENT SEGMENT",
                 [["", "Quarter ended"], ["Efficiency ratio", "83"]])
    summary = Table("B_T2", "Wells Fargo & Company and Subsidiaries SUMMARY FINANCIAL DATA",
                    [["", "Quarter ended"], ["Efficiency ratio (7)", "66"]])
    assert segment_of(page, names) == "wealth and investment management" and segment_of(summary, names) is None
    # A sentence ending in a short capitalised word is no title.
    assert segment_of(Table("T3", "Revenue grew at Wealth and Investment Management in the US", [["", "1Q23"], ["Revenue", "5"]]),
                      names) is None
