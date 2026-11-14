from types import SimpleNamespace

from kpi_extractor.documents.document import parse_document, parse_number
from kpi_extractor.documents.image_pages import UNCONFIRMED, agreed_page, picture_pages, read_image_pages

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


def test_a_figure_stands_only_where_both_models_read_it_alike_in_its_region():
    # ASE's appendix (821 px wide): DeepSeek read 21,932,480 as 21,932,490; RapidOCR read it right. Bradesco's 1,574
    # came back from RapidOCR as 1.574: a thousands group either way, kept as DeepSeek printed it.
    deepseek = BOX.format(kind="table", x0=0, y0=0, x1=500, y1=500,
                          content="<table><tr><td>Net revenues</td><td>21,932,490</td><td>(1,574)</td><td>12.5%</td></tr></table>")
    deepseek += BOX.format(kind="image", x0=500, y0=500, x1=1000, y1=1000, content="")
    words = [(100, 100, 150, 110, "21,932,480"), (200, 100, 240, 110, "(1.574)"), (300, 100, 330, 110, "12.5%"),
             (700, 700, 750, 710, "88.8")]  # a chart's label: DeepSeek gives no words there, so it never stands
    page, kept, masked = agreed_page(deepseek, words, (1000, 1000))
    assert (kept, masked) == (2, 1)
    table = next(iter(parse_document(f"<html><body>{page}</body></html>", "https://x/y.htm").tables.values()))
    assert table.rows[0][1] == UNCONFIRMED and parse_number(table.rows[0][1]) is None
    assert parse_number(table.rows[0][2]).value == -1574 and parse_number(table.rows[0][3]).value == 12.5


def test_a_figure_read_elsewhere_on_the_page_does_not_confirm_it():
    deepseek = BOX.format(kind="text", x0=0, y0=0, x1=300, y1=100, content="Net income 3,155")
    page, kept, masked = agreed_page(deepseek, [(800, 800, 850, 810, "3,155")], (1000, 1000))
    assert (kept, masked) == (0, 1)


def test_swapped_separators_keep_the_reading_whose_separators_agree():
    # Alibaba's form: DeepSeek "19,901,959.514", RapidOCR "19,901,959,514".
    deepseek = BOX.format(kind="text", x0=0, y0=0, x1=1000, y1=1000, content="Balance 19,901,959.514")
    page, kept, _ = agreed_page(deepseek, [(10, 10, 90, 20, "19,901,959,514")], (1000, 1000))
    assert kept == 1 and "19,901,959,514" in page


def test_logos_and_pages_deepseek_returns_empty_are_left_out():
    sizes = {"logo": (120, 60), "p1": (1467, 825), "p2": (1467, 825)}
    texts = {"p1.jpg": "Volume +5.3%", "p2.jpg": ""}
    pages, totals = read_image_pages(
        ["https://x/logo.jpg", "https://x/p1.jpg", "https://x/p2.jpg"], lambda link: link.rsplit("/", 1)[-1].split(".")[0].encode(),
        lambda name, data: (texts[name], False), lambda data: [SimpleNamespace(x0=1, y0=1, x1=9, y1=9, text="+5.3%")],
        lambda data: sizes[data.decode()])
    assert totals == {"pages": 1, "figures kept": 1, "figures masked": 0, "empty pages": 1} and "+5.3%" in pages["https://x/p1.jpg"]


def test_an_exhibit_of_pictures_is_read_through_the_control_planes_ocr_and_untouched_without_it(monkeypatch):
    from kpi_extractor.readers import charts
    from kpi_extractor.steps import loading

    monkeypatch.setattr(loading, "_fetch_page", lambda link: b"page")
    monkeypatch.setattr(loading, "_picture_size", lambda data: (1467, 825))
    monkeypatch.setattr(charts, "ocr_words", lambda data, region=None, scale=3: [SimpleNamespace(x0=1, y0=1, x1=9, y1=9, text="1,089.8")])
    calls = []

    def ocr(symbol, purpose, name, data):
        calls.append(purpose)  # layout first; the page came back empty there, so text-only is asked
        return {"pages": [{"text": "" if purpose == "rich_layout_recognition" else "Volume 1,089.8 MM UC"}]}
    raw = '<html><body><p>Exhibit 99.1</p><img src="kof_001.jpg"><img src="kof_002.jpg"></body></html>'
    reader = SimpleNamespace(symbol="KOF", log=lambda message: None, control=SimpleNamespace(ocr=ocr))
    read = loading.FilingLoading._pictures_read(reader, raw, "https://www.sec.gov/x/kof.htm")
    assert read.count("1,089.8") == 2 and "Exhibit 99.1" in read and "<img" not in read
    assert calls == ["rich_layout_recognition", "text_recognition"] * 2
    plain = SimpleNamespace(symbol="KOF", log=lambda message: None, control=SimpleNamespace())  # code reads: no OCR
    assert loading.FilingLoading._pictures_read(plain, raw, "https://www.sec.gov/x/kof.htm") == raw
