"""The SEC filing archive in R2: each filing's XBRL and earnings exhibits as downloaded, so a later read (a re-read
after a fix, a new KPI looked for in old releases, statements built from history) never goes back to EDGAR.

The runner holds no keys. The control plane lists a company's archived files with links to read them, and hands out a
link per file to write it. Anything that fails here falls back to EDGAR: the archive only ever saves downloads.
"""
from __future__ import annotations

import gzip
import json
import threading
import urllib.request
from dataclasses import dataclass

from .control import ControlError

XBRL_PARTS = ("schema", "label", "presentation", "calculation", "definition", "instance")


class Archive:
    def __init__(self, control, symbol: str, cik: int, log=print):
        self.control, self.symbol, self.cik, self.log = control, symbol, cik, log
        self._lock = threading.Lock()
        self._links: dict[str, str] | None = None
        self._written: set[str] = set()
        self.reads = self.writes = 0

    def key(self, accession: str, name: str) -> str:
        return f"sec/{self.cik}/{accession}/{name}.gz"

    def _listing(self) -> dict[str, str]:
        with self._lock:
            if self._links is None:
                try:
                    self._links = dict(self.control.call("archive_list", symbol=self.symbol, cik=self.cik).get("files") or {})
                except ControlError as error:
                    self.log(f"{self.symbol}: filing archive unavailable ({error}); reading from EDGAR")
                    self._links = {}
            return self._links

    def has(self, accession: str, name: str) -> bool:
        key = self.key(accession, name)
        return key in self._written or key in self._listing()

    def read_json(self, accession: str, name: str) -> dict | None:
        link = self._listing().get(self.key(accession, name))
        if not link:
            return None
        try:
            with urllib.request.urlopen(link, timeout=60) as response:
                data = json.loads(gzip.decompress(response.read()))
        except Exception:  # noqa: BLE001 - a missing or damaged copy is downloaded again
            return None
        self.reads += 1
        return data

    def write_json(self, accession: str, name: str, data: dict) -> None:
        key = self.key(accession, name)
        if key in self._written:
            return
        body = gzip.compress(json.dumps(data).encode(), compresslevel=6)
        try:
            uploads = self.control.call("archive_put", symbol=self.symbol, cik=self.cik,
                                        files=[{"key": key, "size": len(body)}]).get("uploads") or {}
            link = uploads.get(key)
            if not link:
                return
            request = urllib.request.Request(link, data=body, method="PUT", headers={
                "Content-Type": "application/gzip", "Content-Length": str(len(body))})
            with urllib.request.urlopen(request, timeout=120):
                pass
        except Exception as error:  # noqa: BLE001 - not archived this time; the next run tries again
            self.log(f"{self.symbol}: could not archive {key}: {str(error)[:200]}")
            return
        self._written.add(key)
        self.writes += 1


def xbrl_parts(filing) -> dict | None:
    """What edgartools reads to build a filing's XBRL: its linkbases and instance, the header's period of report and
    industry code, and its FilingSummary (statement categories). None when there is nothing to archive."""
    from edgar.xbrl.xbrl import XBRLAttachments
    attachments = XBRLAttachments(filing.attachments)
    parts = {part: attachments.get(part).content for part in XBRL_PARTS if attachments.get(part)}
    if "instance" not in parts:
        return None  # older inline filings fetch the instance another way; those are not archived
    data: dict = {"version": 1, "parts": parts, "period_of_report": None, "sic": None, "filing_summary": None}
    try:
        data["period_of_report"] = str(filing.period_of_report) if filing.period_of_report else None
    except Exception:  # noqa: BLE001
        pass
    try:
        sgml = filing.sgml()
    except Exception:  # noqa: BLE001 - optional context; the facts do not depend on it
        sgml = None
    try:
        header = sgml.header if sgml else None
        if header and header.filers:
            data["sic"] = header.filers[0].company_data.assigned_sic
    except Exception:  # noqa: BLE001
        pass
    try:
        summary = sgml._documents_by_name.get("FilingSummary.xml") if sgml else None
        if summary is not None:
            data["filing_summary"] = summary.content
    except Exception:  # noqa: BLE001
        pass
    return data


_MENU_CATEGORY_TO_CLASSIFICATION = {"Notes": "note", "Tables": "note", "Policies": "note", "Details": "disclosure",
                                    "Cover": "document"}


def xbrl_from_parts(data: dict):
    """The XBRL object edgartools' XBRL.from_filing builds, from archived parts."""
    from edgar.xbrl import XBRL
    xbrl = XBRL()
    parts = data["parts"]
    for part, parse in (("schema", xbrl.parser.parse_schema_content), ("label", xbrl.parser.parse_labels_content),
                        ("presentation", xbrl.parser.parse_presentation_content),
                        ("calculation", xbrl.parser.parse_calculation_content),
                        ("definition", xbrl.parser.parse_definition_content),
                        ("instance", xbrl.parser.parse_instance_content)):
        if parts.get(part):
            parse(parts[part])
    xbrl._sgml_period_of_report = data.get("period_of_report")
    if data.get("sic"):
        try:
            xbrl.standardization.set_industry_from_sic(data["sic"])
        except Exception:  # noqa: BLE001
            pass
    if data.get("filing_summary"):
        try:
            from edgar.sgml.filing_summary import FilingSummary
            summary = FilingSummary.parse(data["filing_summary"])
            summary.reports._filing_summary = summary
            for report in summary.reports:
                if report.role and report.menu_category:
                    if classification := _MENU_CATEGORY_TO_CLASSIFICATION.get(report.menu_category):
                        xbrl._filing_summary_categories[report.role] = classification
                    xbrl._filing_summary_menu_categories[report.role] = report.menu_category
            xbrl._filing_summary = summary
        except Exception:  # noqa: BLE001 - statement categories only
            pass
    return xbrl


@dataclass
class ArchivedExhibit:
    """An earnings exhibit as archived, standing in for edgartools' Attachment where the pipeline reads one."""
    document: str
    document_type: str
    description: str
    url: str
    content: str | None

    def is_html(self) -> bool:
        return True


def exhibits_record(exhibits: list, max_bytes: int) -> dict:
    out = []
    for exhibit in exhibits:
        content = exhibit.content
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        out.append({"document": exhibit.document, "document_type": str(exhibit.document_type or ""),
                    "description": str(getattr(exhibit, "description", "") or ""), "url": exhibit.url,
                    "content": content if content and len(content) <= max_bytes else None})
    return {"version": 1, "exhibits": out}


def exhibits_from_record(data: dict) -> list[ArchivedExhibit]:
    return [ArchivedExhibit(**item) for item in data.get("exhibits") or []]
