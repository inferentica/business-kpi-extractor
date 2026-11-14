from kpi_extractor.evals.ocr_score import classify, deepseek_text, figures


def test_a_figure_is_its_digits_with_its_sign_and_percent_apart_and_period_labels_are_not_figures():
    assert figures("3Q26 +0.8% (357) 1,148 $12,018 FY25 -30 BPS €11.7") == [
        ("0.8", False, True), ("357", True, False), ("1148", False, False), ("12018", False, False), ("30", True, False), ("11.7", False, False)]


def test_deepseeks_box_coordinates_are_never_taken_for_figures():
    text = "<｜ref｜>table<｜/ref｜><｜box｜>[[53,261,511,881]]<｜/box｜> <table><tr><td>+2.1%</td></tr></table>"
    assert figures(deepseek_text([{"text": text}])) == [("2.1", False, True)]


def test_a_misread_is_told_apart_as_a_separator_or_a_digit_error():
    counts, _pairs = classify(["1574", "21932480"], ["1.574", "21932490", "999"])
    assert dict(counts) == {"separator": 1, "digit": 1, "unpaired": 1}
