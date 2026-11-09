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
