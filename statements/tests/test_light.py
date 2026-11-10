"""A filing read from its index page and only the documents its readers open (sources/light.py)."""
from types import SimpleNamespace

import statement_extractor.sources.light as light

PAGE = """<div class="infoHead">Period of Report</div>
<div class="info">2025-12-31</div>
<table class="tableFile" summary="Document Format Files"><tr><th>Seq</th></tr>
<tr><td>1</td><td>FORM 20-F</td><td><a href="/ix?doc=/Archives/edgar/data/1264136/000119312526168715/d82423d20f.htm">d82423d20f.htm</a> &nbsp;&nbsp;iXBRL</td><td>20-F</td><td>27066015</td></tr>
<tr><td>7</td><td>GRAPHIC</td><td><a href="/Archives/edgar/data/1264136/000119312526168715/g1.jpg">g1.jpg</a></td><td>GRAPHIC</td><td>135966</td></tr>
</table>
<table class="tableFile" summary="Data Files"><tr><th>Seq</th></tr>
<tr><td>6</td><td>XBRL TAXONOMY EXTENSION SCHEMA</td><td><a href="/Archives/edgar/data/1264136/000119312526168715/wf-20251231.xsd">wf-20251231.xsd</a></td><td>EX-101.SCH</td><td>9576326</td></tr>
<tr><td>330</td><td>EXTRACTED XBRL INSTANCE DOCUMENT</td><td><a href="/Archives/edgar/data/1264136/000119312526168715/d82423d20f_htm.xml">d82423d20f_htm.xml</a></td><td>XML</td><td>38855917</td></tr>
</table>"""


def test_an_index_page_gives_each_document_and_data_file_and_the_period():
    documents, data, info = light.parse_index(PAGE, "https://www.sec.gov")
    assert [(row["document"], row["type"], row["size"]) for row in documents] == [
        ("d82423d20f.htm", "20-F", 27066015), ("g1.jpg", "GRAPHIC", 135966)]
    assert documents[0]["url"] == "https://www.sec.gov/Archives/edgar/data/1264136/000119312526168715/d82423d20f.htm"
    assert [row["type"] for row in data] == ["EX-101.SCH", "XML"] and info["Period of Report"] == "2025-12-31"


def test_documents_are_fetched_only_when_read_and_a_text_only_filing_is_read_whole(monkeypatch):
    fetched = []
    monkeypatch.setattr(light, "_download", lambda url, as_text: fetched.append(url) or (PAGE if url.endswith("-index.htm") else "<html/>"))
    filing = light.light_filing(SimpleNamespace(accession_no="0001193125-26-168715", cik=1264136, form="20-F"))
    assert len(fetched) == 1 and filing.period_of_report == "2025-12-31"  # the index alone
    assert filing.attachments.documents[0].content == "<html/>" and len(fetched) == 2
    assert [item.document_type for item in filing.attachments.data_files] == ["EX-101.SCH", "XML"]
    whole = """<table class="tableFile" summary="Document Format Files"><tr><th>Seq</th></tr><tr><td>&nbsp;</td>
      <td>Complete submission text file</td><td><a href="/Archives/edgar/data/320193/0000320193-97-000011.txt">0000320193-97-000011.txt</a></td><td>&nbsp;</td><td>300000</td></tr></table>"""
    monkeypatch.setattr(light, "_download", lambda url, as_text: whole)
    assert light.light_filing(SimpleNamespace(accession_no="0000320193-97-000011", cik=320193, form="10-K")) is None
