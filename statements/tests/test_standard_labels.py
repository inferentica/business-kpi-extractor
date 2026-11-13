"""The taxonomies' titles and key lines widen what is recognised (s4-standards, 2026-11-13)."""
from statement_extractor.domain.standard_labels import labels_for, parse_labels

_LABELS = b"""<?xml version="1.0" encoding="utf-8"?>
<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink"><link:labelLink>
<link:loc xlink:label="loc_1" xlink:href="ifrs-full_cor.xsd#ifrs-full_ProfitLossBeforeTax"/>
<link:label xlink:label="lab_1" xlink:role="http://www.xbrl.org/2003/role/label">Ganancia (p\xc3\xa9rdida), antes de impuestos</link:label>
<link:labelArc xlink:from="loc_1" xlink:to="lab_1"/>
<link:loc xlink:label="loc_2" xlink:href="ifrs-full_cor.xsd#ifrs-full_IncomeStatementAbstract"/>
<link:label xlink:label="lab_2" xlink:role="http://www.xbrl.org/2003/role/label">Profit or loss [abstract]</link:label>
<link:labelArc xlink:from="loc_2" xlink:to="lab_2"/>
<link:loc xlink:label="loc_3" xlink:href="ifrs-full_cor.xsd#ifrs-full_StatementOfFinancialPositionAbstract"/>
<link:label xlink:label="lab_3" xlink:role="http://www.xbrl.org/2003/role/label">\xec\x9e\xac\xeb\xac\xb4\xec\x83\x81\xed\x83\x9c\xed\x91\x9c [abstract]</link:label>
<link:labelArc xlink:from="loc_3" xlink:to="lab_3"/>
<link:loc xlink:label="loc_4" xlink:href="ifrs-full_cor.xsd#ifrs-full_CashAndCashEquivalents"/>
<link:label xlink:label="lab_4" xlink:role="http://www.xbrl.org/2003/role/periodEndLabel">Cash and cash equivalents at end of period</link:label>
<link:label xlink:label="lab_5" xlink:role="http://www.xbrl.org/2003/role/label">Cash and cash equivalents</link:label>
<link:labelArc xlink:from="loc_4" xlink:to="lab_4"/><link:labelArc xlink:from="loc_4" xlink:to="lab_5"/>
</link:labelLink></link:linkbase>"""


def test_labels_read_from_a_label_linkbase():
    labels = labels_for({"ifrs-es": parse_labels(_LABELS)})
    assert labels["pretax"] == {"Ganancia (pérdida), antes de impuestos"}
    assert labels["title:balance"] == {"재무상태표"}
    assert "title:income" not in labels or not labels["title:income"]  # "Profit or loss" is prose, never a title
    assert labels["cash:periodEndLabel"] == {"Cash and cash equivalents at end of period"}  # never the balance sheet's cash line


def test_widened_patterns_take_a_standards_label_whole():
    import re
    from statement_extractor.domain.printed_labels import Widened
    pattern = Widened(re.compile(r"^net\s+income$", re.I))
    pattern.widen({"Ganancia (pérdida)"})
    assert pattern.search("Ganancia (pérdida)") and pattern.match("Net income")
    assert not pattern.search("Ganancia (pérdida) por acción")
    title = Widened(re.compile(r"\bbalance\s+sheets?\b", re.I))
    title.widen({"Estado de situación financiera"}, whole=False)
    assert [match.group(0) for match in title.finditer("Grupo X Estado de situación financiera al 31 de diciembre")] == [
        "Estado de situación financiera"]
