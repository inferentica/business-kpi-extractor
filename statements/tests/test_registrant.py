from statement_extractor.pipeline import printed


class _Filing:
    cik, company = "0", ""


def _other(cover: str, names: list[str]):
    printed._NAMES["0"] = names

    class Cover:
        document_type, content = "6-K", cover

    class Ref:
        filing = _Filing

    return printed.another_registrant(Ref, [Cover])


def test_a_subsidiarys_report_is_another_registrants():
    assert _other("23 October 2024 Commission File number 001-35079 LLOYDS BANK plc (Translation of registrant's name",
                  ["Lloyds Banking Group plc", "LLOYDS TSB GROUP PLC"]) == "LLOYDS BANK plc"


def test_the_companys_own_name_in_any_form_is_its_own():
    assert _other("Commission File Number 1-15106 PETR&Oacute;LEO BRASILEIRO S.A. - PETROBRAS (Exact name of registrant",
                  ["PETROLEO BRASILEIRO SA PETROBRAS"]) is None
    assert _other("(Commission File No. 1-14668) Energy Company of Paran&aacute; (Translation of registrant's name",
                  ["COMPANHIA PARANAENSE DE ENERGIA - COPEL"]) is None  # in English: shares no word, never set aside
    assert _other("Commission File number 001-15246 L LOYDS BANKING GROUP plc (Translation of registrant's name",
                  ["Lloyds Banking Group plc"]) is None
    assert _other("May 2005 Commission File Number: 033-97038 BRASCAN CORPORATION (Translation of registrant's name",
                  ["BROOKFIELD Corp /ON/", "BROOKFIELD ASSET MANAGEMENT INC.", "BRASCAN CORP/"]) is None


def test_a_letter_lost_in_decoding_keeps_the_name():
    assert _other("Commission File Number 1-15106 PETR�LEO BRASILEIRO S.A. - PETROBRAS (Exact name of registrant",
                  ["PETROLEO BRASILEIRO SA PETROBRAS"]) is None


def _others(titles: list[str], names: list[str], cover: str | None = None) -> list[str]:
    printed._NAMES["0"] = names

    class Reading:
        def __init__(self, titled):
            self.titled = titled

    class Ref:
        filing = _Filing

    return printed._other_companies([Reading(title) for title in titles], Ref, cover)


def test_an_acquired_companys_statements_are_anothers():
    # Gildan's 6-K of June 2011: Gold Toe Moretz's statements, from Gildan's business acquisition report.
    cover = ("Commission File Number: 1-14830 GILDAN ACTIVEWEAR INC. (Translation of Registrant's name into English) "
             "Indicate by check mark … 99.2 Consolidated Financial Statements … of Gold Toe Moretz Holdings Corp.")
    assert _others(["GOLD TOE MORETZ HOLDINGS CORP."], ["GILDAN ACTIVEWEAR INC."], cover) == ["GOLD TOE MORETZ HOLDINGS CORP."]


def test_a_title_in_the_companys_own_name_is_its_own():
    assert _others(["Gildan Activewear Inc."], ["GILDAN ACTIVEWEAR INC."]) == []
    assert _others(["BRASCAN CORPORATION"], ["BROOKFIELD Corp /ON/", "BRASCAN CORP/"]) == []  # a former name
    cover = "(Commission File No. 1-14668) COMPANHIA PARANAENSE DE ENERGIA - COPEL Energy Company of Parana Indicate by check mark"
    assert _others(["COMPANHIA PARANAENSE DE ENERGIA - COPEL"], ["ENERGY CO OF PARANA"], cover) == []  # the cover's name


def test_a_name_in_the_companys_own_language_or_a_page_fragment_is_never_anothers():
    cover = "Commission File Number 333-8618 FOMENTO ECONÓMICO MEXICANO, S.A.B. DE C.V. Indicate by check mark"
    assert _others(["FOMENTO ECONÓMICO MEXICANO, S.A.B. DE C.V."], ["MEXICAN ECONOMIC DEVELOPMENT INC"], cover) == []
    assert _others(["C.V.", "Sul", "KEPCO"], ["MEXICAN ECONOMIC DEVELOPMENT INC"], cover) == []  # under two name words


def test_an_annual_report_is_never_checked():
    assert _others(["GRUPO AEROPORTUARIO DEL SURESTE, S.A.B. DE C.V."], ["SOUTHEAST AIRPORT GROUP"], None) == []


def test_a_listed_subsidiarys_results_are_anothers():
    # CEMEX's 6-Ks: CEMEX Holdings Philippines' and CEMEX Latam's results beside CEMEX's own.
    cover = "Commission File Number: 001-14946 CEMEX, S.A.B. de C.V. (Exact name of Registrant) CEMEX Corp. Indicate by check mark"
    names = ["CEMEX SAB DE CV"]
    assert _others(["CEMEX Holdings Philippines, Inc.", "CEMEX Latam Holdings, S.A.", "CEMEX, S.A.B. de C.V."], names, cover) == [
        "CEMEX Holdings Philippines, Inc.", "CEMEX Latam Holdings, S.A."]
    ultrapar = "Commission File Number 001-14950 ULTRAPAR HOLDINGS INC. (Translation of Registrant's Name) Indicate by check mark"
    assert _others(["Ultrapar Participações S.A.", "Chief Executive Officer ULTRAPAR HOLDINGS INC."], ["ULTRAPAR HOLDINGS INC"],
                   ultrapar) == []  # its Portuguese name, and a signature's words before its name
    brookfield = "Commission File Number 033-97038 BROOKFIELD ASSET MANAGEMENT INC. (Translation) Indicate by check mark"
    assert _others(["BRASCAN CORPORATION", "Brookfield Asset Management Inc."], ["BROOKFIELD Corp /ON/", "BRASCAN CORP/"],
                   brookfield) == []


def test_a_release_opening_under_a_brand_is_the_companys_own():
    # Volaris's releases open "Volaris*", its legal name Controladora Vuela Compañía de Aviación; FEMSA's 6-Ks carry
    # Coca-Cola FEMSA's release, opening with its legal name.
    printed._NAMES["0"] = ["Controladora Vuela Compania de Aviacion, S.A.B. de C.V."]

    class Reading:
        def __init__(self, release_of):
            self.release_of = release_of

    class Ref:
        filing = _Filing

    cover = "CONTROLADORA VUELA COMPAÑÍA DE AVIACIÓN, S.A.B. DE C.V. (Exact name of registrant as specified in its charter)"
    readings = [Reading(name) for name in ("Volaris*", "| Page", "Coca-Cola FEMSA, S.A.B. de C.V.")]
    assert printed._other_issuers(readings, Ref, cover) == ["Coca-Cola FEMSA, S.A.B. de C.V."]


def test_each_name_on_the_cover_is_compared():
    # América Móvil: "AMÉRICA MÓVIL, S.A.B. DE C.V. (Exact Name of the Registrant as Specified in the Charter) America
    # Mobile (Translation of Registrant's Name into English)".
    cover = ("Commission File Number: 1-16269 AM&Eacute;RICA M&Oacute;VIL, S.A.B. DE C.V. (Exact Name of the Registrant as "
             "Specified in the Charter) America Mobile (Translation of Registrant&rsquo;s Name into English) Lago Alberto 366")
    assert _other(cover, ["America Movil SAB de CV"]) is None
