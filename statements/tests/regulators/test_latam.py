"""BMV's and CMF's IFRS-tagged quarterly filings read as statements through the IFRS taxonomy's roles."""
import io
import json
import zipfile
from datetime import date

from statement_extractor.domain.ifrs_roles import parse_role
from statement_extractor.readers.regulators.bmv import bmv_facts
from statement_extractor.readers.regulators.cmf import cmf_facts
from statement_extractor.readers.regulators.tagged import Fact, tagged_readings

ROLES = {"balance": [[("StatementOfFinancialPositionAbstract", 0), ("CurrentAssets", 1), ("NoncurrentAssets", 1),
                      ("Assets", 1), ("Liabilities", 1), ("Equity", 1), ("EquityAndLiabilities", 1)]],
         "income": [[("Revenue", 0), ("CostOfSales", 0), ("GrossProfit", 0), ("ProfitLoss", 0),
                     ("ProfitLossAttributableToOwnersOfParent", 1), ("ProfitLossAttributableToNoncontrollingInterests", 1)],
                    [("Revenue", 0), ("EmployeeBenefitsExpense", 0), ("RawMaterialsAndConsumablesUsed", 0),
                     ("DepreciationAndAmortisationExpense", 0), ("OtherExpenseByNature", 0), ("ProfitLoss", 0)]],
         "cash_flow": [[("CashFlowsFromUsedInOperatingActivities", 0), ("CashFlowsFromUsedInInvestingActivities", 0),
                        ("CashFlowsFromUsedInFinancingActivities", 0), ("IncreaseDecreaseInCashAndCashEquivalents", 0),
                        ("CashAndCashEquivalents", 0)]]}
Q3 = (date(2025, 7, 1), date(2025, 9, 30))


def sqm_facts():
    # SQM's Q3 2025 on CMF (thousands of US dollars, filed as full units), with expenses by nature from its notes.
    facts = [Fact(name, *Q3, value * 1000, "USD", -3) for name, value in (
        ("Revenue", 1173047), ("CostOfSales", 827239), ("GrossProfit", 345808), ("ProfitLoss", 180048),
        ("ProfitLossAttributableToOwnersOfParent", 178000), ("ProfitLossAttributableToNoncontrollingInterests", 2048),
        ("EmployeeBenefitsExpense", 115064), ("RawMaterialsAndConsumablesUsed", 351413),
        ("DepreciationAndAmortisationExpense", 107690), ("OtherExpenseByNature", 311131))]
    facts += [Fact(name, None, date(2025, 9, 30), value * 1000, "USD", -3) for name, value in (
        ("CurrentAssets", 5521857), ("NoncurrentAssets", 6204355), ("Assets", 11726212), ("Liabilities", 6194339),
        ("Equity", 5531873), ("EquityAndLiabilities", 11726212), ("CashAndCashEquivalents", 1491181))]
    return facts


def test_a_quarter_by_function_is_laid_out_by_function_though_its_notes_tag_expenses_by_nature():
    readings = tagged_readings(sqm_facts(), ROLES, {"Revenue": "Revenue"}, accession="CMF-93007000-202509-20251118",
                               form="CMF-Q3", filed=date(2025, 11, 18), document_end=date(2025, 9, 30),
                               fiscal_year_end="1231", year_offset=0, source="cmf")
    income = next(r for r in readings if r.statement == "income")
    assert [line["concept"] for line in income.lines][:3] == ["ifrs-full_Revenue", "ifrs-full_CostOfSales", "ifrs-full_GrossProfit"]
    assert (income.key, income.currency, income.status, income.lines[0]["label_en"]) == (
        ("income", "2025", "Q3", "quarter"), "USD", "kept", "Revenue")
    balance = next(r for r in readings if r.statement == "balance")
    assert balance.status == "kept" and balance.own


def test_bmv_and_cmf_facts_skip_dimensions_and_other_taxonomies():
    report = {"ContextosPorId": {"c1": {"Periodo": {"FechaInicio": "2025-01-01T00:00:00Z", "FechaFin": "2025-09-30T00:00:00Z"},
                                        "ContieneInformacionDimensional": False},
                                 "c2": {"Periodo": {"FechaInstante": "2025-09-30T00:00:00Z"}, "ContieneInformacionDimensional": True}},
              "UnidadesPorId": {"u": {"Medidas": [{"Nombre": "MXN", "EspacioNombres": "http://www.xbrl.org/2003/iso4217"}]}},
              "HechosPorId": {"a": {"IdConcepto": "ifrs-full_Revenue", "IdContexto": "c1", "IdUnidad": "u", "ValorNumerico": 42879.1, "Decimales": "-3"},
                              "b": {"IdConcepto": "ifrs-full_Assets", "IdContexto": "c2", "IdUnidad": "u", "ValorNumerico": 1.0},
                              "c": {"IdConcepto": "ifrs_mx-cor_20141205_TotalDeCreditos", "IdContexto": "c1", "IdUnidad": "u", "ValorNumerico": 2.0}}}
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("ifrsxbrl_1_2025-03_1.json", json.dumps(report))
    assert bmv_facts(package.getvalue()) == [Fact("Revenue", date(2025, 1, 1), date(2025, 9, 30), 42879.1, "MXN", -3)]
    instance = b"""<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:ifrs-full="https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full" xmlns:cl-ci="https://www.cmfchile.cl/cl/fr/ci">
      <xbrli:context id="p1"><xbrli:entity><xbrli:identifier scheme="x">93007000</xbrli:identifier></xbrli:entity>
        <xbrli:period><xbrli:instant>2025-09-30</xbrli:instant></xbrli:period></xbrli:context>
      <xbrli:context id="d1"><xbrli:entity><xbrli:identifier scheme="x">93007000</xbrli:identifier><xbrli:segment>
        <xbrldi:explicitMember dimension="ifrs-full:SegmentsAxis">ifrs-full:X</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
        <xbrli:period><xbrli:instant>2025-09-30</xbrli:instant></xbrli:period></xbrli:context>
      <xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
      <ifrs-full:Assets contextRef="p1" unitRef="USD" decimals="-3">11726212000</ifrs-full:Assets>
      <ifrs-full:Assets contextRef="d1" unitRef="USD" decimals="-3">5</ifrs-full:Assets>
      <cl-ci:Otro contextRef="p1" unitRef="USD" decimals="-3">7</cl-ci:Otro></xbrli:xbrl>"""
    assert cmf_facts(instance) == [Fact("Assets", None, date(2025, 9, 30), 11726212000.0, "USD", -3)]


def test_a_presentation_role_is_walked_in_its_order():
    role = b"""<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink">
      <link:presentationLink><link:loc xlink:label="a" xlink:href="x.xsd#ifrs-full_StatementAbstract"/>
      <link:loc xlink:label="b" xlink:href="x.xsd#ifrs-full_Revenue"/><link:loc xlink:label="c" xlink:href="x.xsd#ifrs-full_ProfitLoss"/>
      <link:presentationArc xlink:from="a" xlink:to="c" order="2"/><link:presentationArc xlink:from="a" xlink:to="b" order="1"/>
      </link:presentationLink></link:linkbase>"""
    assert parse_role(role) == [("StatementAbstract", 0), ("Revenue", 1), ("ProfitLoss", 1)]


def test_cmf_2010_statements_on_the_older_ifrs_namespace_are_read():
    instance = b"""<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:ifrs="http://xbrl.iasb.org/taxonomy/2009-04-01/ifrs">
      <xbrli:context id="p1"><xbrli:entity><xbrli:identifier scheme="x">93007000</xbrli:identifier></xbrli:entity>
        <xbrli:period><xbrli:instant>2010-06-30</xbrli:instant></xbrli:period></xbrli:context>
      <xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
      <ifrs:Assets contextRef="p1" unitRef="USD" decimals="-3">4000000000</ifrs:Assets></xbrli:xbrl>"""
    assert cmf_facts(instance) == [Fact("Assets", None, date(2010, 6, 30), 4000000000.0, "USD", -3)]
