"""Releases naming no statement are remembered between runs, under the finder's title patterns."""
from types import SimpleNamespace

from statement_extractor.pipeline.empty_releases import names_a_statement, remember, remembered


def document(name, content):
    return SimpleNamespace(document=name, content=content)


def test_a_notice_names_no_statement_and_a_release_does():
    notice = [document("notice.htm", b"<p>Petrobras on the payment of dividends</p>"), document("logo.jpg", b"\xff\xd8")]
    release = [document("release.htm", "<p>Consolidated Balance<br>Sheet</p><table><tr><td>Cash</td></tr></table>")]
    split = [document("r.htm", "<p>Statement of Cash Fl<span>ows</span></p>")]
    assert not names_a_statement(notice)
    assert names_a_statement(release) and names_a_statement(split)


def test_a_pdf_is_never_judged_by_its_bytes():
    assert names_a_statement([document("q1.pdf", b"%PDF-1.4 ...")])


def test_the_list_round_trips_and_needs_a_folder(tmp_path):
    remember(str(tmp_path), {"0001-24-000001"})
    assert remembered(str(tmp_path)) == {"0001-24-000001"}
    (tmp_path / "empty-releases-old.json").write_text('["x"]')
    remember(str(tmp_path), {"0001-24-000002"})
    assert sorted(p.name for p in tmp_path.iterdir()) == [p.name for p in tmp_path.iterdir() if p.name != "empty-releases-old.json"]
    assert remembered(None) == set() and remember(None, {"a"}) is None
