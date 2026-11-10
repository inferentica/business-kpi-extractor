"""Statements from home regulators' data: CVM's forms and OpenDART's reports, their checks, and where they rank."""
from datetime import date

from statement_extractor.domain.ifrs_labels import parse_labels
from statement_extractor.domain.model import Reading
from statement_extractor.readers.regulators.cvm import cvm_readings
from statement_extractor.readers.regulators.dart import dart_readings
from statement_extractor.selection.choose import choose


def cvm_row(code, name, value, start="2018-07-01", end="2018-09-30", order="ÚLTIMO", fixed="S"):
    return {"CNPJ_CIA": "76.483.817/0001-20", "DT_REFER": "2018-09-30", "VERSAO": "1", "MOEDA": "REAL",
            "ESCALA_MOEDA": "MIL", "ORDEM_EXERC": order, "DT_INI_EXERC": start, "DT_FIM_EXERC": end,
            "CD_CONTA": code, "DS_CONTA": name, "VL_CONTA": str(value), "ST_CONTA_FIXA": fixed}


INDEX = [{"CNPJ_CIA": "76.483.817/0001-20", "DT_REFER": "2018-09-30", "VERSAO": "1", "ID_DOC": "78839",
          "DT_RECEB": "2018-11-08"}]
# Copel's Q3 2018 group income, three months, from CVM's ITR file (thousands of reais).
COPEL_Q3 = [("3.01", "Receita de Venda de Bens e/ou Serviços", 4309126), ("3.02", "Custo dos Bens e/ou Serviços Vendidos", -3459741),
            ("3.03", "Resultado Bruto", 849385), ("3.04", "Despesas/Receitas Operacionais", -251184),
            ("3.04.01", "Despesas com Vendas", -30580), ("3.04.02", "Despesas Gerais e Administrativas", -167458),
            ("3.04.04", "Outras Receitas Operacionais", 66608), ("3.04.05", "Outras Despesas Operacionais", -180910),
            ("3.04.06", "Resultado de Equivalência Patrimonial", 61156), ("3.05", "Resultado Antes do Resultado Financeiro", 598201)]


def test_a_cvm_quarter_keeps_its_codes_names_and_scale_and_its_tree_adds_up():
    readings = cvm_readings("ITR", {"index": INDEX, "DRE_con": [cvm_row(*row) for row in COPEL_Q3]}, "1231", 0)
    [reading] = readings
    assert (reading.key, reading.own, reading.currency, reading.scope, reading.status) == (
        ("income", "2018", "Q3", "quarter"), True, "BRL", "consolidated", "kept")
    assert (reading.accession, reading.filed) == ("CVM-ITR-78839-v1", date(2018, 11, 8))
    revenue = reading.lines[0]
    assert (revenue["code"], revenue["concept"], revenue["value"]) == ("3.01", "cvm_Revenue", 4_309_126_000.0)


def test_a_cvm_account_whose_parts_do_not_add_up_is_rejected_and_a_company_detail_is_not_summed():
    wrong = [cvm_row(*row) for row in COPEL_Q3]
    wrong[4] = cvm_row("3.04.01", "Despesas com Vendas", -40580)
    assert cvm_readings("ITR", {"index": INDEX, "DRE_con": wrong}, "1231", 0)[0].status == "rejected"
    partial = [cvm_row(*row) for row in COPEL_Q3] + [cvm_row("3.04.06.01", "Uma participada", 1000, fixed="N")]
    assert cvm_readings("ITR", {"index": INDEX, "DRE_con": partial}, "1231", 0)[0].status == "kept"


def dart_row(sj, tag, name, **amounts):
    return {"rcept_no": "20181114002722", "sj_div": sj, "account_id": tag, "account_nm": name, "ord": "1",
            "currency": "KRW", **amounts}


def test_a_dart_quarter_gives_its_three_months_its_year_to_date_and_last_years():
    rows = [dart_row("CIS", "ifrs_Revenue", "수익(매출액)", thstrm_amount="16409844000000", thstrm_add_amount="45453047000000",
                     frmtrm_q_amount="16187727000000", frmtrm_add_amount="44259875000000"),
            dart_row("CIS", "ifrs-full_ProfitLoss", "당기순이익", thstrm_amount="100", thstrm_add_amount="300",
                     frmtrm_q_amount="90", frmtrm_add_amount="250"),
            dart_row("CIS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "지배", thstrm_amount="80",
                     thstrm_add_amount="260", frmtrm_q_amount="70", frmtrm_add_amount="200"),
            dart_row("CIS", "ifrs-full_ProfitLossAttributableToNoncontrollingInterests", "비지배", thstrm_amount="20",
                     thstrm_add_amount="40", frmtrm_q_amount="20", frmtrm_add_amount="50")]
    found = {(r.fiscal_year, r.fiscal_period, r.duration, r.own): r for r in dart_readings(rows, 2018, "11014", "CFS", "1231", 0)}
    assert set(found) == {("2018", "Q3", "quarter", True), ("2018", "Q3", "ytd", True), ("2017", "Q3", "quarter", False),
                          ("2017", "Q3", "ytd", False)}
    quarter = found[("2018", "Q3", "quarter", True)]
    assert quarter.status == "kept" and quarter.filed == date(2018, 11, 14) and quarter.lines[0]["concept"] == "ifrs-full_Revenue"
    assert found[("2017", "Q3", "ytd", False)].lines[0]["value"] == 44_259_875_000_000


def test_a_dart_cash_flow_whose_change_is_not_closing_less_opening_is_rejected():
    # KEPCO's Q1 2018 report on OpenDART: a change of 1,002,107 against closing less opening of 210,783.
    rows = [dart_row("CF", "ifrs_IncreaseDecreaseInCashAndCashEquivalents", "순증가", thstrm_amount="1002107000000"),
            dart_row("CF", "dart_CashAndCashEquivalentsAtBeginningOfPeriodCf", "기초", thstrm_amount="2369739000000"),
            dart_row("CF", "dart_CashAndCashEquivalentsAtEndOfPeriodCf", "기말", thstrm_amount="2580522000000")]
    own = [r for r in dart_readings(rows, 2018, "11013", "CFS", "1231", 0) if r.own]
    assert own[0].status == "rejected"


def reading(source, form, filed, period="Q3", value=1.0, currency="BRL"):
    return Reading(statement="income", duration="quarter" if period != "FY" else "year", fiscal_year="2018",
                   fiscal_period=period, period_end=date(2018, 9, 30) if period != "FY" else date(2018, 12, 31),
                   period_start=date(2018, 7, 1) if period != "FY" else date(2018, 1, 1), currency=currency,
                   accession=f"{source}-{filed}", form=form, filed=filed, own=True, source=source, basis="IFRS",
                   lines=[{"line": 0, "concept": "x_Revenue", "label": "Revenue", "level": 0, "is_heading": False,
                           "value": value}], checks=[])


def test_a_regulator_quarter_wins_over_a_later_release_and_a_20f_year_over_the_regulators():
    from statement_extractor.domain.model import Check
    def kept(item):
        item.checks = [Check("calc", "pass")]
        return item
    quarter = choose([kept(reading("cvm", "ITR", date(2018, 11, 8), value=4.0)),
                      kept(reading("html", "6-K", date(2019, 1, 20), value=4.0))])
    assert quarter[0]["source_form"] == "ITR"
    year = choose([kept(reading("cvm", "DFP", date(2019, 3, 20), "FY")), kept(reading("html", "20-F", date(2019, 4, 30), "FY")),
                   kept(reading("cvm", "DFP", date(2020, 3, 20), "FY"))])
    assert year[0]["source_form"] == "20-F"


def test_ifrs_labels_are_read_from_the_label_linkbase():
    content = b"""<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink">
      <link:labelLink><link:loc xlink:href="../full_ifrs-cor.xsd#ifrs-full_Revenue" xlink:label="loc_1"/>
      <link:label xlink:label="res_1" xlink:role="http://www.xbrl.org/2003/role/label">Revenue</link:label>
      <link:label xlink:label="res_2" xlink:role="http://www.xbrl.org/2003/role/totalLabel">Total revenue</link:label>
      <link:labelArc xlink:from="loc_1" xlink:to="res_1"/><link:labelArc xlink:from="loc_1" xlink:to="res_2"/>
      </link:labelLink></link:linkbase>"""
    assert parse_labels(content) == {"Revenue": "Revenue"}


def test_a_regulator_and_an_sec_filing_do_not_witness_each_other():
    from statement_extractor.selection.witnesses import _alike
    assert not _alike(reading("dart", "DART-Q1", date(2024, 5, 16)), reading("html", "6-K", date(2024, 6, 13)))
    assert _alike(reading("cvm", "ITR", date(2018, 5, 15)), reading("cvm", "ITR", date(2020, 12, 17)))


def test_a_cvm_statement_filed_empty_is_no_statement():
    empty = [cvm_row(code, name, 0) for code, name, _value in COPEL_Q3]
    assert cvm_readings("ITR", {"index": INDEX, "DRE_con": empty}, "1231", 0) == []
