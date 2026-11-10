from statement_extractor.readers.positioned.layout import is_positioned, positioned_text


def page(number: int, runs: str) -> str:
    return f'<div id="Page{number}" style="position:relative;width:794px;height:1123px;">{runs}</div>'


def run(left: float, top: float, text: str) -> str:
    return f'<div style="position:absolute;font-size:10px;left:{left}px;top:{top}px;">{text}</div>'


def test_pages_of_positioned_text_lay_out_as_a_statement():
    # UBS's quarterly reports since 2023: each line a div placed by its left and top, the footer naming the section.
    # Figures right-aligned as printed: a longer one starts further left.
    rows = [("Net interest income", "1,940", "2,095"), ("Total revenues", "12,739", "10,855")]
    body = run(60, 100, "Income statement") + run(60, 120, "USD m") + run(480, 120, "31.3.24") + run(530, 120, "31.12.23")
    for index, (label, a, b) in enumerate(rows):
        top = 140 + 16 * index
        body += run(60, top, label) + run(511 - 5 * len(a), top, a) + run(561 - 5 * len(b), top, b)
    body += run(60, 1080, "Consolidated financial statements | UBS Group AG interim consolidated financial statements 54")
    content = "<body style=\"font-size:10px;\">" + "".join(page(n, body if n == 2 else run(60, 100, "Notes" * (300 if n == 1 else 1)))
                                                         for n in (1, 2, 3)) + "</body>"
    text = positioned_text(content)
    lines = text.split("\n<PAGE>\n")[1].split("\n")
    assert lines[0].strip().startswith("Consolidated financial statements")  # the footer is the page's context
    assert lines[1].strip() == "Income statement"
    figures = [line for line in lines if "Net interest income" in line][0]
    assert figures.split()[-2:] == ["1,940", "2,095"]
    total = [line for line in lines if "Total revenues" in line][0]
    assert figures.index("2,095") + len("2,095") == total.index("10,855") + len("10,855")  # right edges line up
