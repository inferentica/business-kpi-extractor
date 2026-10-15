"""TSMC's platform pie: one quarter's pie on the left of a slide titled in its hidden text, growth rates on the right."""
from kpi_extractor.charts import Word, read_titled_pie, titled_pie_slides

# 3Q26 Revenue by Platform, as the OCR reads the slide (IoT comes back as "101"; the growth chart's signed rates and
# its run-together labels stand on the right half).
WORDS = [Word(*box, text) for *box, text in [
    (1273, 27, 1341, 50, "tsmc"), (89, 74, 666, 118, "3Q26RevenuebyPlatform"),
    (804, 183, 1150, 209, "GrowthRatebyPlatform(QoQ)"), (354, 233, 463, 253, "Automotive"), (297, 248, 332, 271, "101"),
    (464, 252, 509, 273, "DCE"), (997, 246, 1055, 272, "+23%"), (394, 254, 425, 277, "4%"), (732, 258, 785, 280, "+22%"),
    (300, 268, 330, 291, "4%"), (470, 274, 502, 297, "1%"), (506, 281, 574, 306, "Others"), (527, 306, 554, 327, "2%"),
    (214, 365, 332, 388, "Smartphone"), (819, 362, 876, 388, "+10%"), (252, 387, 294, 410, "21%"),
    (915, 407, 959, 433, "+5%"), (429, 437, 476, 462, "HPC"), (1091, 445, 1139, 467, "DCE"), (1171, 444, 1240, 468, "Others"),
    (431, 459, 474, 485, "68%"), (736, 500, 1082, 523, "HPC SmartphoneloTAutomotive"), (1182, 579, 1229, 601, "-10%"),
    (1090, 597, 1141, 622, "-12%"), (1267, 745, 1355, 767, "TSMCProperty"),
]]


def test_the_platform_pie_pairs_each_share_with_the_label_over_it():
    pie = read_titled_pie(WORDS, "platform", "2026", "Q3")
    assert pie is not None and pie.subject == "Net revenue"
    assert pie.shares == {"High Performance Computing": 68, "Smartphone": 21, "Internet of Things": 4, "Automotive": 4,
                          "Digital Consumer Electronics": 1, "Others": 2}


def test_a_pie_missing_a_platform_or_not_adding_up_is_left_out():
    assert read_titled_pie([word for word in WORDS if word.text != "DCE" or word.x0 > 700], "platform", "2026", "Q3") is None
    misread = [Word(word.x0, word.y0, word.x1, word.y1, "86%" if word.text == "68%" else word.text) for word in WORDS]
    assert read_titled_pie(misread, "platform", "2026", "Q3") is None


def test_the_slide_is_found_by_its_hidden_title():
    html = ('<IMG src="a3q26presentatione006.jpg" title="slide6"><DIV><FONT size="1">Unleash Innovation &copy; 52026 TSMC, '
            'Ltd TSMC Property 3Q26 Revenue by Platform </FONT></DIV>'
            '<IMG src="a3q26presentatione005.jpg"><DIV><FONT size="1">3Q26 Revenue by Technology</FONT></DIV>')
    assert titled_pie_slides(html) == [("a3q26presentatione006.jpg", "platform", "2026", "Q3"),
                                       ("a3q26presentatione005.jpg", "technology", "2026", "Q3")]


# 3Q26 Revenue by Technology as the OCR reads it: "40/45nm" runs into 65nm's "4%", and "90nm-" stands over "0.13um".
TECHNOLOGY = [Word(*box, text) for *box, text in [
    (88, 69, 732, 124, "3Q26RevenuebyTechnology"), (917, 184, 1169, 208, "7nmandBelowRevenue"),
    (407, 209, 468, 232, "90nm-"), (402, 230, 472, 253, "0.13um"), (349, 238, 402, 256, "65nm"), (424, 254, 452, 274, "1%"),
    (477, 250, 559, 273, "≥0.15um"), (504, 273, 532, 293, "2%"), (266, 251, 390, 280, "40/45nm4%"),
    (736, 267, 785, 289, "1,200"), (160, 279, 240, 302, "16/20nm"), (294, 276, 322, 296, "2%"), (186, 302, 214, 322, "6%"),
    (314, 325, 370, 348, "28nm"), (477, 340, 521, 361, "2nm"), (326, 346, 358, 369, "6%"), (217, 360, 261, 384, "7nm"),
    (482, 360, 514, 383, "8%"), (219, 382, 259, 405, "10%"), (464, 430, 509, 452, "3nm"), (274, 441, 321, 464, "5nm"),
    (466, 451, 508, 474, "33%"), (278, 463, 319, 485, "28%"), (910, 588, 964, 606, "7nm"), (981, 588, 1038, 606, "5nm"),
    (1267, 745, 1354, 767, "TSMCProperty"),
]]


def test_the_technology_pie_takes_apart_run_together_labels():
    pie = read_titled_pie(TECHNOLOGY, "technology", "2026", "Q3")
    assert pie is not None and pie.subject == "Wafer revenue"
    # As TSMC's management report gives them for 3Q26.
    assert pie.shares == {"2nm": 8, "3nm": 33, "5nm": 28, "7nm": 10, "16nm": 6, "28nm": 6, "40/45nm": 2, "65nm": 4,
                          "90nm-0.13 Micron": 1, "0.15 Micron and Above": 2}


def test_the_growth_chart_gives_each_platform_its_rate_left_to_right():
    from kpi_extractor.charts import read_growth_rates
    assert read_growth_rates(WORDS) == {"High Performance Computing": 22, "Smartphone": 10, "Internet of Things": 5,
                                        "Automotive": 23, "Digital Consumer Electronics": -12, "Others": -10}
    assert read_growth_rates([word for word in WORDS if word.text != "+5%"]) is None
