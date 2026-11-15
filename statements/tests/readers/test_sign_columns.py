from datetime import date

from statement_extractor.readers.html.read import read_document


def _row(label, *cells):
    return "<tr><td>" + label + "</td>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def _income(scale="Amounts expressed in millions of U.S. dollars ($) and in millions of Mexican pesos (Ps.)."):
    # FEMSA's FY2011 20-F, cut down: "2011" heads the dollar translation and the pesos, each sign beside its figures.
    return ("<p>FOMENTO ECON&#211;MICO MEXICANO, S.A.B. DE C.V. AND SUBSIDIARIES Consolidated Income Statements For the "
            f"years ended December 31, 2011 and 2010. {scale}</p><table>"
            "<tr><td></td><td colspan='4'>2011</td><td colspan='2'>2010</td></tr>"
            + _row("Net sales", "$", "14,470", "Ps.", "201,867", "Ps.", "168,376")
            + _row("Cost of sales", "", "8,459", "", "118,009", "", "98,732")
            + _row("Gross profit", "", "6,011", "", "83,858", "", "69,644")
            + _row("Income before income taxes", "", "2,033", "", "28,371", "", "23,632")
            + _row("Income taxes", "", "507", "", "7,077", "", "5,671")
            + _row("Net income", "$", "1,526", "Ps.", "21,294", "Ps.", "17,961") + "</table>")


def test_a_heading_over_a_translation_and_the_pesos_gives_each_its_column():
    readings, notes = read_document(_income(), accession="0001193125-12-191729", form="20-F", filed=date(2012, 4, 27),
                                    document_end=date(2011, 12, 31), fiscal_year_end="1231", year_offset=0)
    sales = {reading.fiscal_year: next(line["value"] for line in reading.lines if line["label"] == "Net sales")
             for reading in readings}
    assert sales == {"2011": 201_867e6, "2010": 168_376e6}  # the dollar column left out, never read as 2011
    assert {reading.currency for reading in readings} == {"MXN"}
    assert any("convenience translation" in note for note in notes)


def test_signs_the_title_does_not_define_leave_the_column_as_it_was():
    readings, _notes = read_document(_income("Amounts expressed in millions of Mexican pesos."), accession="x", form="20-F",
                                     filed=date(2012, 4, 27), document_end=date(2011, 12, 31), fiscal_year_end="1231",
                                     year_offset=0)
    assert all(next(line["value"] for line in reading.lines if line["label"] == "Net sales") != 201_867e6
               for reading in readings if reading.fiscal_year == "2011")


def test_a_sign_under_the_heading_names_its_columns_currency():
    # FEMSA's FY2024 20-F: each heading spans three cells; its first row prints the sign in the heading's first cell and
    # the figure in the next two, later rows the figure across all three. The signs went unseen, the title's dollars were
    # taken for the reporting currency and the pesos left out (round 82).
    def first(label, *pairs):
        return "<tr><td colspan='3'>" + label + "</td>" + "".join(
            f"<td>{sign}</td><td colspan='2'>{figure}</td>" for sign, figure in pairs) + "</tr>"

    def later(label, *figures):
        return "<tr><td colspan='3'>" + label + "</td>" + "".join(f"<td colspan='3'>{figure}</td>" for figure in figures) + "</tr>"
    html = ("<p>FOMENTO ECON&#211;MICO MEXICANO, S.A.B. DE C.V. AND SUBSIDIARIES Consolidated Income Statements For the "
            "years ended December 31, 2024 and 2023. In millions of U.S. dollars ($) and in millions of Mexican pesos (Ps.).</p>"
            "<table><tr><td colspan='3'></td><td colspan='3'>2024 (1)</td><td colspan='3'>2024</td><td colspan='3'>2023</td></tr>"
            + first("Net sales", ("$", "38,300"), ("Ps.", "781,600"), ("Ps.", "702,700"))
            + later("Cost of sales", "23,000", "469,500", "421,000") + later("Gross profit", "15,300", "312,100", "281,700")
            + later("Income before income taxes", "3,143", "64,140", "58,000") + later("Income taxes", "1,000", "20,400", "18,500")
            + later("Net income", "2,143", "43,740", "39,500") + "</table>")
    readings, notes = read_document(html, accession="x", form="20-F", filed=date(2025, 4, 25), document_end=date(2024, 12, 31),
                                    fiscal_year_end="1231", year_offset=0)
    sales = {r.fiscal_year: (r.currency, next(line["value"] for line in r.lines if line["label"] == "Net sales")) for r in readings}
    assert sales == {"2024": ("MXN", 781_600e6), "2023": ("MXN", 702_700e6)}
