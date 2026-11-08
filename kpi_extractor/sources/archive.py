"""The SEC filing archive in R2: each filing's XBRL and earnings exhibits as downloaded, so a later read (a re-read
after a fix, a new KPI looked for in old releases, statements built from history) never goes back to EDGAR.

Layout: filings/<cik>/company.json names the company; filings/<cik>/<filed>_<form>_<accession>/ holds a filing's
xbrl.json.gz or exhibits.json.gz. The runner holds no keys. The control plane lists a company's archived files with links to read them, and hands out a
link per file to write it. Anything that fails here falls back to EDGAR: the archive only ever saves downloads.
"""
from __future__ import annotations

import gzip
import json
import threading
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..ops.control import ControlError

XBRL_PARTS = ("schema", "label", "presentation", "calculation", "definition", "instance")
# Bedrock's worker fetches each tracked company's filing list every hour; an older one may miss a filing SEC has listed.
FILING_LIST_AGE = timedelta(minutes=90)


class Archive:
    def __init__(self, control, symbol: str, cik: int, log=print):
        self.control, self.symbol, self.cik, self.log = control, symbol, cik, log
        self._lock = threading.Lock()
        self._links: dict[str, str] | None = None
        self._listed_at: dict[str, str] = {}  # when each filing list in the archive was written
        self._written: set[str] = set()
        self._refused = False  # the archive turned a write down: the rest of the run reads from EDGAR only
        self.reads = self.writes = 0
        self.submissions = 0  # full submissions read from the archive instead of SEC
        self.lists = 0  # filing lists read from the archive instead of SEC

    def key(self, folder: str, name: str) -> str:
        return f"filings/{self.cik}/{folder}/{name}.gz"

    def _listing(self) -> dict[str, str]:
        with self._lock:
            if self._links is None:
                try:
                    listing = self.control.call("archive_list", symbol=self.symbol, cik=self.cik)
                    self._links = dict(listing.get("files") or {})
                    self._listed_at = dict(listing.get("written") or {})
                except ControlError as error:
                    self.log(f"{self.symbol}: filing archive unavailable ({str(error)[:200]}); reading from EDGAR")
                    self._links, self._refused = {}, True
            return self._links

    def has(self, folder: str, name: str) -> bool:
        key = self.key(folder, name)
        return key in self._written or key in self._listing()

    def read_json(self, folder: str, name: str) -> dict | None:
        return self._read_gzipped_json(self.key(folder, name))

    def _read_gzipped_json(self, key: str) -> dict | None:
        link = self._listing().get(key)
        if not link:
            return None
        try:
            with urllib.request.urlopen(link, timeout=60) as response:
                data = json.loads(gzip.decompress(response.read()))
        except Exception:  # noqa: BLE001 - a missing or damaged copy is downloaded again
            return None
        self.reads += 1
        return data

    def filing_list_key(self, name: str | None = None) -> str:
        """Where the company's SEC filing list (CIK0000320193.json) or one of its older pages sits."""
        return f"filings/{self.cik}/submissions/{name or f'CIK{self.cik:010d}.json'}.gz"

    def filing_list(self, now: datetime | None = None) -> tuple[dict | None, str]:
        """The company's SEC filing list as Bedrock's worker keeps it here, its older pages merged in (as edgartools
        merges a bulk download's), and why not when it cannot stand in for SEC's: missing, written more than 90 minutes
        ago, an older page missing, or a filing whose full submission was archived since and that it does not list."""
        key = self.filing_list_key()
        links = self._listing()
        written = self._listed_at.get(key)
        if key not in links or not written:
            return None, "no archived filing list"
        try:
            age = (now or datetime.now(timezone.utc)) - datetime.fromisoformat(written.replace("Z", "+00:00"))
        except ValueError:
            return None, "archived filing list has no write time"
        if age > FILING_LIST_AGE:
            return None, f"archived filing list is {age.total_seconds() / 3600:.1f} h old"
        data = self._read_gzipped_json(key)
        filings = (data or {}).get("filings") or {}
        recent = filings.get("recent")
        if not isinstance(recent, dict) or not isinstance(recent.get("accessionNumber"), list):
            return None, "archived filing list unreadable"
        for page in filings.get("files") or []:
            older = self._read_gzipped_json(self.filing_list_key(str(page.get("name") or "")))
            if not isinstance(older, dict) or not isinstance(older.get("accessionNumber"), list):
                return None, f"older page {page.get('name')} not archived"
            count = len(older["accessionNumber"])
            for field, values in recent.items():
                column = older.get(field)
                values.extend(column if isinstance(column, list) and len(column) == count else [None] * count)
        filings["files"] = []
        listed = set(recent["accessionNumber"])
        prefix = f"filings/{self.cik}/raw/"
        unlisted = [stored[len(prefix):-len(".txt.gz")] for stored in links
                    if stored.startswith(prefix) and stored.endswith(".txt.gz")
                    and stored[len(prefix):-len(".txt.gz")] not in listed]
        if unlisted:
            return None, f"archived filing list lacks {unlisted[0]}, filed since"
        self.lists += 1
        return data, ""

    def raw_submission_key(self, accession: str) -> str:
        """Where a filing's full submission sits as SEC served it, put there by the filing feed's worker."""
        return f"filings/{self.cik}/raw/{accession}.txt.gz"

    def read_submission(self, accession: str) -> str | None:
        """A filing's full submission text from the archive, or None when the worker has not put it there."""
        link = self._listing().get(self.raw_submission_key(accession))
        if not link:
            return None
        try:
            with urllib.request.urlopen(link, timeout=180) as response:
                text = gzip.decompress(response.read()).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - read from SEC instead
            return None
        self.reads += 1
        self.submissions += 1
        return text

    def write_json(self, folder: str, name: str, data: dict) -> None:
        self._put(self.key(folder, name), gzip.compress(json.dumps(data).encode(), compresslevel=6), "application/gzip")

    def label(self, symbol: str, name: str) -> None:
        """filings/<cik>/company.json: who the folder is (its tickers and name), for anyone browsing the bucket. The
        folder keeps the CIK, which never changes; a new ticker for the same company is added to the label."""
        key = f"filings/{self.cik}/company.json"
        current: dict = {}
        if link := self._listing().get(key):
            try:
                with urllib.request.urlopen(link, timeout=30) as response:
                    current = json.loads(response.read())
            except Exception:  # noqa: BLE001 - rewritten below
                current = {}
        tickers = list(dict.fromkeys([*(current.get("tickers") or []), symbol]))
        label = {"cik": self.cik, "name": name, "tickers": tickers}
        if current != label:
            self._put(key, json.dumps(label, indent=1).encode(), "application/json")

    def _put(self, key: str, body: bytes, content_type: str) -> None:
        self._listing()  # an archive that cannot be listed is not written to either
        if key in self._written or self._refused:
            return
        try:
            uploads = self.control.call("archive_put", symbol=self.symbol, cik=self.cik,
                                        files=[{"key": key, "size": len(body)}]).get("uploads") or {}
            link = uploads.get(key)
            if not link:
                return
            request = urllib.request.Request(link, data=body, method="PUT", headers={
                "Content-Type": content_type, "Content-Length": str(len(body))})
            with urllib.request.urlopen(request, timeout=120):
                pass
        except Exception as error:  # noqa: BLE001 - not archived this time; the next run tries again
            self._refused = True
            self.log(f"{self.symbol}: could not archive {key}: {str(error)[:200]}; not archiving the rest of this run")
            return
        self._written.add(key)
        self.writes += 1


def filing_folder(filed, form: str, accession: str) -> str:
    """A filing's folder, readable and in date order: "2026-08-27_10-Q_0001045810-26-000123"."""
    return f"{str(filed)[:10]}_{form.replace('/', '-')}_{accession}"


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
