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
    assert titled_pie_slides(html) == [("a3q26presentatione006.jpg", "platform", "2026", "Q3")]


def test_the_growth_chart_gives_each_platform_its_rate_left_to_right():
    from kpi_extractor.charts import read_growth_rates
    assert read_growth_rates(WORDS) == {"High Performance Computing": 22, "Smartphone": 10, "Internet of Things": 5,
                                        "Automotive": 23, "Digital Consumer Electronics": -12, "Others": -10}
    assert read_growth_rates([word for word in WORDS if word.text != "+5%"]) is None
