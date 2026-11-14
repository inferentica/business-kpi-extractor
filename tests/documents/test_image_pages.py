from types import SimpleNamespace

from kpi_extractor.documents.document import parse_document, parse_number
from kpi_extractor.documents.image_pages import (DEEPSEEK, LOCAL, ONE_READER, SUMS_TO_AGREED_TOTAL, agreed_page, hold_single_reads,
                                                picture_pages, read_image_pages)

BOX = "<｜ref｜>{kind}<｜/ref｜><｜box｜>[[{x0},{y0},{x1},{y1}]]<｜/box｜> {content}\n"


def test_a_page_picture_is_one_with_no_words_of_its_own_beneath_it():
    # Ambev's deck: page pictures under a 6-K cover whose dates and file numbers are its only figures.
    cover = "<p>Commission File Number 001-36908, October 29, 2026, 1017 Dr. Renato Paes de Barros, 4th floor</p>"
    pictures = f'<html><body>{cover}<img src="abev_001.jpg"><img src="abev_002.jpg"></body></html>'
    assert [source for _tag, source in picture_pages(pictures, "https://www.sec.gov/Archives/edgar/data/1565025/x/abev.htm")] == [
        "https://www.sec.gov/Archives/edgar/data/1565025/x/abev_001.jpg", "https://www.sec.gov/Archives/edgar/data/1565025/x/abev_002.jpg"]
    # ASML's slides keep their words beneath each picture; a chart in Coca-Cola FEMSA's release sits among its paragraphs.
    worded = '<img src="s1.jpg"><p>Net sales 7,516 9,718 8,767</p><img src="s2.jpg"><p>Gross margin 51.6 52.2 53.0</p>'
    assert picture_pages(worded, "https://www.sec.gov/x/asml.htm") == []
    # TSMC's deck: two pie slides with no words beneath, among worded slides: left to the chart reader.
    deck = worded + '<img src="pie1.jpg"><p>Revenue by Technology</p><img src="pie2.jpg"><p>Revenue by Platform</p>' + worded
    assert picture_pages(deck, "https://www.sec.gov/x/tsm.htm") == []


def test_a_figure_both_models_read_alike_is_agreed_and_one_read_by_one_model_is_listed():
    # ASE's appendix (821 px wide): DeepSeek read 21,932,480 as 21,932,490; RapidOCR read it right. Bradesco's 1,574
    # came back from RapidOCR as 1.574: a thousands group either way, agreed as DeepSeek printed it.
    deepseek = BOX.format(kind="table", x0=0, y0=0, x1=500, y1=500,
                          content="<table><tr><td>Net revenues</td><td>21,932,490</td><td>(1,574)</td><td>12.5%</td></tr></table>")
    deepseek += BOX.format(kind="image", x0=500, y0=500, x1=1000, y1=1000, content="")
    words = [(100, 100, 150, 110, "21,932,480"), (200, 100, 240, 110, "(1.574)"), (300, 100, 330, 110, "12.5%"),
             (700, 700, 750, 710, "EBITDA 88.8")]  # a chart's lettering: DeepSeek gives no words there
    page, kept, single = agreed_page(deepseek, words, (1000, 1000))
    assert kept == 2 and single == {"21932490": DEEPSEEK, "888": LOCAL}
    document = parse_document(f"<html><body>{page}</body></html>", "https://x/y.htm")
    table = next(iter(document.tables.values()))
    assert table.rows[0][1:] == ["21,932,490", "(1,574)", "12.5%"] and "EBITDA 88.8" in document.render()


def test_a_figure_read_elsewhere_on_the_page_does_not_agree_with_it():
    deepseek = BOX.format(kind="text", x0=0, y0=0, x1=300, y1=100, content="Net income 3,155")
    _page, kept, single = agreed_page(deepseek, [(800, 800, 850, 810, "3,155")], (1000, 1000))
    assert kept == 0 and single == {"3155": DEEPSEEK}


def test_swapped_separators_keep_the_reading_whose_separators_agree():
    # Alibaba's form: DeepSeek "19,901,959.514", RapidOCR "19,901,959,514".
    deepseek = BOX.format(kind="text", x0=0, y0=0, x1=1000, y1=1000, content="Balance 19,901,959.514")
    page, kept, single = agreed_page(deepseek, [(10, 10, 90, 20, "19,901,959,514")], (1000, 1000))
    assert kept == 1 and not single and "19,901,959,514" in page


def test_logos_are_left_out_and_a_page_deepseek_returns_empty_stands_on_rapidocrs_words():
    sizes = {"logo": (120, 60), "p1": (1467, 825), "p2": (1467, 825)}
    texts = {"p1.jpg": "Volume +5.3%", "p2.jpg": ""}
    pages, single, totals = read_image_pages(
        ["https://x/logo.jpg", "https://x/p1.jpg", "https://x/p2.jpg"], lambda link: link.rsplit("/", 1)[-1].split(".")[0].encode(),
        lambda name, data: (texts[name], False), lambda data: [SimpleNamespace(x0=1, y0=1, x1=9, y1=9, text="+5.3%")],
        lambda data: sizes[data.decode()])
    assert totals == {"pages": 2, "empty pages": 1, "figures agreed": 1, "figures one model read": 1}
    assert "+5.3%" in pages["https://x/p1.jpg"] and "+5.3%" in pages["https://x/p2.jpg"] and single == {"53": LOCAL}


def _value(group, key, value, cell, total=False):
    from kpi_extractor.ai import GroupSpec, KpiSpec
    return SimpleNamespace(group=group, kpi=KpiSpec(key=key, label=key, unit="currency"), value=value, is_total=total,
                           status="verified", notes=[], locator={"table": "T0", "row": cell, "col": 1})


def test_a_value_one_model_alone_read_is_held_unless_its_breakdown_adds_up_to_a_total_both_read():
    from kpi_extractor.ai import GroupSpec, KpiSpec
    group = GroupSpec(key="bu", label="Revenue by Business Unit", kind="revenue_breakdown", total_kpi="total",
                      kpis=[KpiSpec(key=k, label=k, unit="currency") for k in ("beer", "nab", "total")])
    document = parse_document("<table><tr><td>Beer</td><td>10,138</td></tr><tr><td>NAB</td><td>2,213</td></tr>"
                              "<tr><td>Total</td><td>12,351</td></tr></table>", "https://x/y.htm")
    document.single_reads = {"2213": DEEPSEEK}
    read = [_value(group, "beer", 10138e6, 0), _value(group, "nab", 2213e6, 1), _value(group, "total", 12351e6, 2, total=True)]
    hold_single_reads(read, document)
    assert [item.status for item in read] == ["verified"] * 3 and read[1].notes == [f"{ONE_READER} (DeepSeek); {SUMS_TO_AGREED_TOTAL}"]
    # The total one model alone read proves nothing: the part is held, naming the model.
    document.single_reads = {"2213": DEEPSEEK, "12351": LOCAL}
    read = [_value(group, "beer", 10138e6, 0), _value(group, "nab", 2213e6, 1), _value(group, "total", 12351e6, 2, total=True)]
    hold_single_reads(read, document)
    assert read[1].status == "needs_review" and read[1].notes == [f"{ONE_READER} (DeepSeek)"]
    assert read[2].status == "needs_review" and read[2].notes == [f"{ONE_READER} (RapidOCR)"]


def _classifier(monkeypatch, previous=None, answer="earnings_release"):
    from kpi_extractor.steps import kpi_list, loading

    monkeypatch.setattr(loading, "_fetch_page", lambda link: link.encode())  # each page its own picture
    monkeypatch.setattr(kpi_list, "classify_by_rules", lambda excerpt: None)  # the AI decides, and is shown the pages
    deck = '<html><body><p>Commission File Number 001-36908</p><img src="abev_001.jpg"><img src="abev_002.jpg"></body></html>'
    exhibit = SimpleNamespace(url="https://www.sec.gov/x/abev.htm", document_type="6-K", document="abev.htm")
    asked, recorded, ocr = [], [], []
    reader = SimpleNamespace(
        symbol="ABEV", company="Ambev", filings={"acc": previous} if previous else {},
        source=SimpleNamespace(is_own_document=lambda e: True, exhibit_html=lambda e: deck,
                               classification_excerpt=lambda ref: "Form 6-K: Commission File Number 001-36908"),
        control=SimpleNamespace(ocr=lambda symbol, purpose, name, data: ocr.append(name) or {"pages": [{"text": "3Q26 Earnings Call Net revenue +7.6%"}]}),
        store=SimpleNamespace(record=recorded.append),
        _ask=lambda prompt, *a, **k: asked.append(prompt) or SimpleNamespace(kind=answer))
    reader._picture_excerpt = lambda ref: kpi_list.KpiListUpkeep._picture_excerpt(reader, ref)
    reader._page_ocr = lambda purpose, name, data: loading.FilingLoading._page_ocr(reader, purpose, name, data)
    ref = SimpleNamespace(confirmed=False, accession="acc", exhibits=[exhibit])
    return kpi_list.KpiListUpkeep._is_earnings_document(reader, ref), asked, recorded, ocr


def test_a_deck_of_pictures_is_classified_with_its_opening_pages_read(monkeypatch):
    # Ambev's results presentation: its 6-K's only words are the cover; the classifier skipped it as no release.
    is_release, asked, _recorded, ocr = _classifier(monkeypatch)
    assert is_release and ocr == ["abev_001.jpg", "abev_002.jpg"] and "3Q26 Earnings Call" in asked[0]


def test_a_deck_skipped_on_its_cover_alone_is_classified_again_once(monkeypatch):
    from kpi_extractor.steps.kpi_list import _CLASSIFIED_PAST_COVER, _CLASSIFIED_PICTURES, _NOT_EARNINGS

    old = {"status": "skipped", "notes": [_NOT_EARNINGS, _CLASSIFIED_PAST_COVER]}
    is_release, asked, _recorded, _ocr = _classifier(monkeypatch, previous=old)
    assert is_release and asked
    checked = {"status": "skipped", "notes": [_NOT_EARNINGS, _CLASSIFIED_PAST_COVER, _CLASSIFIED_PICTURES]}
    is_release, asked, _recorded, ocr = _classifier(monkeypatch, previous=checked)
    assert not is_release and not asked and not ocr
