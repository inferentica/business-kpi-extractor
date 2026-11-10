"""A filing read from only the documents the readers open, in place of its whole submission.

edgartools downloads a filing's complete submission before its attachments or XBRL can be read: every image, PDF and
SEC rendering with it (Woori's 2025 20-F: 120.6 MB, of which its statements need the XBRL instance and schema, 48 MB,
or its printed report). Here the filing's index page (SEC's -index.htm, a few KB) lists each document's sequence, type,
name and size, and a document's content is fetched only when a reader opens it, through edgartools' own downloader
(SEC's identity, rate limit and retries). XBRL is built by edgartools from the same attachments it would pick from the
submission (XBRL.from_filing over the index's data files), so it reads the same.

A filing whose index lists no document of its own (EDGAR's paper-era and early text filings: one complete submission
file) is read whole, as before."""
from __future__ import annotations

import html
import re
from datetime import date
from pathlib import PurePosixPath

_ARCHIVES = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{name}"
_TABLE = re.compile(r'<table class="tableFile"[^>]*summary="([^"]+)">(.*?)</table>', re.S)
_INFO = re.compile(r'<div class="infoHead">([^<]+)</div>\s*<div class="info">([^<]+)</div>')
_TEXT = (".htm", ".html", ".txt", ".xml", ".xsd", ".json", ".csv")


def _download(url: str, as_text: bool):
    from edgar.httprequests import download_file

    from .retry import with_network_retries
    return with_network_retries(lambda: download_file(url, as_text=as_text))


class LightAttachment:
    """One document of a filing, as edgartools' Attachment presents it to the readers; its content is fetched once,
    when first read."""

    def __init__(self, url: str, sequence: str, description: str, document: str, document_type: str, size: int):
        self.url, self.sequence_number, self.description = url, sequence, description
        self.document, self.document_type, self.size = document, document_type, size
        self._content = None

    @property
    def extension(self) -> str:
        return PurePosixPath(self.document).suffix

    @property
    def content(self):
        if self._content is None:
            text = self.document.lower().endswith(_TEXT)
            self._content = _download(self.url, as_text=text)
        return self._content

    def __repr__(self) -> str:
        return f"LightAttachment({self.document_type} {self.document})"


class LightAttachments(list):
    """A filing's attachments in index order, its XBRL data files apart as edgartools keeps them."""

    def __init__(self, documents: list[LightAttachment], data_files: list[LightAttachment]):
        super().__init__(documents + data_files)
        self.documents, self.data_files = documents, data_files


class LightFiling:
    """A filing whose attachments come from its index; anything else is asked of the edgartools filing it wraps."""

    def __init__(self, filing, attachments: LightAttachments, period: date | None):
        self._filing, self.attachments, self._period = filing, attachments, period

    @property
    def period_of_report(self):
        return self._period.isoformat() if self._period else getattr(self._filing, "period_of_report", None)

    def xbrl(self):
        from edgar.xbrl import XBRL
        return XBRL.from_filing(self) if self.attachments.data_files else None

    def __getattr__(self, name):
        return getattr(self._filing, name)


def parse_index(page: str, base: str) -> tuple[list[dict], list[dict], dict[str, str]]:
    """(documents, data files, header facts) from a filing's -index.htm: each row's sequence, description, file name,
    type, size and address."""
    tables: dict[str, list[dict]] = {}
    for summary, body in _TABLE.findall(page):
        rows = []
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)[1:]:
            cells = [html.unescape(re.sub(r"<[^>]+>", "", cell)).replace("\xa0", " ").strip()
                     for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            link = re.search(r'href="([^"]+)"', row)
            if len(cells) < 5 or link is None:
                continue
            href = link.group(1).replace("/ix?doc=", "")
            name = cells[2].split()[0] if cells[2] else PurePosixPath(href).name
            size = int(re.sub(r"\D", "", cells[4]) or 0)
            rows.append({"sequence": cells[0], "description": cells[1], "document": name, "type": cells[3],
                         "size": size, "url": href if href.startswith("http") else base + href})
        tables[summary] = rows
    info = {key.strip(): value.strip() for key, value in _INFO.findall(page)}
    return tables.get("Document Format Files", []), tables.get("Data Files", []), info


def light_filing(filing) -> LightFiling | None:
    """The filing read document by document, or None where its index lists only the complete submission."""
    folder = str(filing.accession_no).replace("-", "")
    index_url = _ARCHIVES.format(cik=int(filing.cik), folder=folder, name=f"{filing.accession_no}-index.htm")
    documents, data_files, info = parse_index(_download(index_url, as_text=True) or "", "https://www.sec.gov")
    own = [row for row in documents if not row["document"].endswith(".txt") or row["type"] not in ("", "Complete submission text file")]
    if not own or all(row["description"].lower().startswith("complete submission") for row in own):
        return None
    make = lambda row: LightAttachment(row["url"], row["sequence"], row["description"], row["document"], row["type"], row["size"])  # noqa: E731
    period = info.get("Period of Report")
    try:
        period_day = date.fromisoformat(period) if period else None
    except ValueError:
        period_day = None
    return LightFiling(filing, LightAttachments([make(row) for row in own], [make(row) for row in data_files]), period_day)
