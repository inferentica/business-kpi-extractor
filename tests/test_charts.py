"""ASML's "Net system sales breakdown" slide: pies whose labels and shares exist only in the picture."""
from kpi_extractor.charts import Word, breakdown_slides, read_breakdowns, read_units

# The words a local OCR model reads on ASML's Q2 2026 slide 8, with their boxes [x0, y0, x1, y1, text].
SLIDE = [
    [45, 62, 902, 108, "Net system sales breakdown (quarterly)"],
    [217, 140, 350, 171, "Technology"],
    [539, 142, 635, 168, "End-use"],
    [720, 141, 989, 170, "Region(ship to location)"],
    [1071, 146, 1292, 175, "Salesinlithography"],
    [373, 169, 456, 191, "ArFDry4%"],
    [1153, 176, 1213, 200, "units"],
    [387, 199, 438, 217, "KrF6%"],
    [918, 197, 988, 216, "Japan4%"],
    [55, 226, 146, 250, "Q22026"],
    [388, 223, 448, 240, "1-line1%"],
    [835, 224, 879, 245, "China"],
    [236, 237, 271, 256, "ArFi"],
    [387, 246, 472, 267, "Metrology&"],
    [834, 242, 871, 261, "14%"],
    [946, 237, 1005, 254, "USA9%"],
    [23, 257, 183, 280, "Netsystemsales"],
    [235, 253, 271, 272, "29%"],
    [386, 262, 483, 281, "Inspection3%"],
    [1237, 259, 1263, 281, "35"],
    [520, 273, 562, 291, "Logic"],
    [611, 272, 668, 293, "Memory"],
    [38, 282, 161, 306, "6.6billion"],
    [777, 273, 831, 293, "Taiwan"],
    [520, 289, 557, 307, "51%"],
    [612, 289, 646, 308, "49%"],
    [778, 290, 812, 309, "30%"],
    [1102, 286, 1131, 310, "23"],
    [1038, 304, 1064, 325, "16"],
    [279, 326, 312, 344, "EUV"],
    [831, 327, 921, 349, "SouthKorea"],
    [1176, 326, 1188, 342, "8"],
    [1309, 323, 1322, 338, "9"],
    [278, 343, 312, 361, "57%"],
    [833, 345, 868, 364, "43%"],
    [1031, 382, 1065, 400, "EUV"],
    [1097, 382, 1134, 400, "ArFi"],
    [1154, 383, 1208, 401, "ArFdry"],
    [1233, 382, 1265, 401, "KrF"],
    [1295, 383, 1337, 401, "I-line"],
    [384, 452, 462, 472, "ArFDry2%"],
    [921, 457, 1004, 474, "RestofAsia"],
    [384, 476, 435, 494, "KrF6%"],
    [921, 472, 947, 491, "1%"],
    [56, 497, 145, 518, "Q12026"],
    [259, 497, 293, 516, "ArFi"],
    [382, 497, 444, 517, "1-line1%"],
    [827, 488, 872, 509, "China"],
    [258, 514, 293, 532, "23%"],
    [827, 506, 862, 524, "19%"],
    [382, 516, 467, 539, "Metrology&"],
    [946, 511, 1011, 528, "USA12%"],
    [22, 524, 183, 550, "Net system sales"],
    [382, 533, 479, 553, "Inspection2%"],
    [768, 538, 822, 558, "Taiwan"],
    [38, 551, 161, 575, "6.3billion"],
    [516, 560, 560, 581, "Logic"],
    [614, 557, 670, 578, "Memory"],
    [768, 555, 803, 574, "23%"],
    [1238, 555, 1264, 576, "30"],
    [517, 577, 552, 595, "49%"],
    [614, 575, 650, 593, "51%"],
    [243, 590, 276, 608, "EUV"],
    [1038, 588, 1063, 609, "16"],
    [1104, 587, 1129, 607, "17"],
    [242, 607, 276, 625, "66%"],
    [819, 604, 905, 621, "SouthKorea"],
    [1304, 601, 1328, 621, "11"],
    [818, 619, 853, 638, "45%"],
    [1175, 616, 1190, 635, "5"],
    [1031, 666, 1066, 684, "EUV"],
    [1098, 666, 1134, 685, "ArFi"],
    [1152, 663, 1211, 687, "ArFdry"],
    [1233, 666, 1265, 685, "KrF"],
    [1295, 665, 1338, 686, "I-line"],
    [40, 726, 105, 750, "ASML"],
    [141, 732, 217, 749, "July 15,2026"],
    [1320, 734, 1357, 748, "Page8"],
    [1322, 746, 1358, 764, "Public"],
]


def _charts(words=SLIDE):
    return {(chart.axis, chart.fiscal_period, chart.fiscal_year): chart for chart in read_breakdowns([Word(*word) for word in words])}


def test_each_pie_of_each_quarter_is_read_with_its_shares():
    charts = _charts()
    assert set(charts) == {(axis, quarter, "2026") for axis in ("technology", "end-use", "region") for quarter in ("Q1", "Q2")}
    assert charts[("technology", "Q2", "2026")].shares == {
        "EUV": 57, "ArFi": 29, "KrF": 6, "ArF Dry": 4, "Metrology & Inspection": 3, "I-line": 1}
    assert charts[("technology", "Q1", "2026")].shares["EUV"] == 66
    assert charts[("end-use", "Q2", "2026")].shares == {"Logic": 51, "Memory": 49}
    # A label over its share ("China" / "14%"), lettering that lost its spaces, and members that differ by quarter.
    assert charts[("region", "Q2", "2026")].shares == {"South Korea": 43, "Taiwan": 30, "China": 14, "USA": 9, "Japan": 4}
    assert charts[("region", "Q1", "2026")].shares == {"South Korea": 45, "Taiwan": 23, "China": 19, "USA": 12, "Rest of Asia": 1}
    assert all(chart.subject == "Net system sales" for chart in charts.values())


def test_a_pie_that_does_not_add_up_to_a_whole_is_left_out():
    # The picture read wrongly for one slice (57% as 37%): that pie is dropped, the others stand.
    words = [[*word[:4], "37%" if word[4] == "57%" else word[4]] for word in SLIDE]
    charts = _charts(words)
    assert ("technology", "Q2", "2026") not in charts
    assert ("technology", "Q1", "2026") in charts and ("region", "Q2", "2026") in charts


def test_only_breakdown_slides_are_picked_from_a_presentation():
    html = (
        '<IMG src="deck007.jpg" title="slide7"> <DIV><FONT size="1">Page 7 Q2 results summary Total net sales</FONT></DIV>'
        '<IMG src="deck008.jpg" title="slide8"> <DIV><FONT size="1">Page 8 Net system sales breakdown (quarterly) Q2 2026 '
        'Net system sales &#8364;6.6 billion</FONT></DIV>')
    assert breakdown_slides(html) == [("deck008.jpg", "Page 8 Net system sales breakdown (quarterly) Q2 2026 Net system sales €6.6 billion")]


def test_older_slides_label_quarters_with_two_digit_years_and_squash_the_title():
    older = {"Q22026": "Q2'26", "Q12026": "Q1'26", "Net system sales breakdown (quarterly)": "Net systemsalesbreakdown"}
    charts = _charts([[*word[:4], older.get(word[4], word[4])] for word in SLIDE])
    assert charts[("technology", "Q2", "2026")].shares["EUV"] == 57
    assert charts[("region", "Q1", "2026")].shares["Rest of Asia"] == 1
    assert len(charts) == 6


def test_the_bars_of_units_sold_are_read_for_each_quarter():
    units = {(chart.fiscal_period, chart.fiscal_year): chart for chart in read_units([Word(*word) for word in SLIDE])}
    assert units[("Q2", "2026")].units == {"EUV": 16, "ArFi": 23, "ArF Dry": 8, "KrF": 35, "I-line": 9}
    assert units[("Q1", "2026")].units == {"EUV": 16, "ArFi": 17, "ArF Dry": 5, "KrF": 30, "I-line": 11}
    assert units[("Q2", "2026")].missing is None


def test_a_bar_whose_count_the_picture_did_not_give_up_is_named_and_two_such_bars_drop_the_chart():
    # Q2's "8" over the ArF Dry bar goes unread.
    one = [word for word in SLIDE if not (word[4] == "8" and word[1] < 400)]
    units = {(chart.fiscal_period, chart.fiscal_year): chart for chart in read_units([Word(*word) for word in one])}
    assert units[("Q2", "2026")].missing == "ArF Dry" and "ArF Dry" not in units[("Q2", "2026")].units
    two = [word for word in one if not (word[4] == "9" and word[1] < 400)]
    assert ("Q2", "2026") not in {(chart.fiscal_period, chart.fiscal_year) for chart in read_units([Word(*word) for word in two])}


def test_an_older_slide_calls_south_korea_korea():
    words = [[*word[:4], "Korea" if word[4] == "SouthKorea" else word[4]] for word in SLIDE]
    assert "South Korea" in _charts(words)[("region", "Q2", "2026")].shares
