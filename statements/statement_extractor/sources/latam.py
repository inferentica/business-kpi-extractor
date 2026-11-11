"""Mexico's exchange (BMV) and Chile's regulator (CMF): where a US-listed company's quarterly IFRS XBRL is filed at home.

BMV lists every issuer's quarterly report packages on one page (the last 20 quarters; older ones only through a
scripted service, not read yet): each package's id, quarter, version and the time BMV received it. CMF answers a query
per company, quarter and basis with the period's files, its XBRL among them, and a log of each submission's time; its
site answers the runners, not every network. Everything fetched is kept in the run's cache; a quarter of this year or
last is asked again each run, as late filings and resubmissions land there."""
from __future__ import annotations

import html
import http.cookiejar
import io
import json
import re
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime

from .regulators import DOWN, KEPT_SERVED, _cache, _fresh, _get

_BMV = "https://www.bmv.com.mx"
_CMF = "https://www.cmfchile.cl/institucional/mercados/"
_AGENT = {"User-Agent": "TradeTracker statements (admin@inferentica.com)"}
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_PACKAGE = re.compile(r"ifrsxbrl_(\d+)_(\d{4})-(\d{2})(d?)_(\d+)\.zip")


def bmv_packages(key: str) -> list[dict]:
    """An issuer's quarterly packages on BMV's XBRL page: id, quarter end, audited (a year-end "d" report), version and
    when BMV received it. The page is fetched once a day."""
    cache = _cache()
    kept = cache / "bmv_listing.html" if cache else None
    if kept is not None and kept.exists() and time.time() - kept.stat().st_mtime < 86400:
        page = kept.read_text(encoding="utf-8")
    else:
        try:
            page = _get(f"{_BMV}/es/emisoras/archivos-estadar-xbrl").decode("utf-8", "replace")
        except OSError:
            if kept is None or not kept.exists():
                raise
            KEPT_SERVED.append(kept.name)  # BMV unreachable: the last listing kept, not none
            page = kept.read_text(encoding="utf-8")
        else:
            if kept is not None:
                kept.parent.mkdir(parents=True, exist_ok=True)
                kept.write_text(page, encoding="utf-8")
    found = []
    for row in _ROW.findall(html.unescape(page)):
        cells = [re.sub(r"<[^>]+>", " ", cell).strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        package = _PACKAGE.search(row)
        if not cells or cells[0] != key or package is None:
            continue
        received = re.search(r"(\d{2})/(\d{2})/(\d{4})", row)
        doc_id, year, quarter, audited, version = package.groups()
        month = int(quarter) * 3
        found.append({"id": doc_id, "end": date(int(year), month, 30 if month in (6, 9) else 31), "audited": bool(audited),
                      "version": int(version), "file": package.group(0),
                      "received": date(int(received.group(3)), int(received.group(2)), int(received.group(1))) if received else None})
    return found


def bmv_package(package: dict) -> bytes:
    """A package's zip, fetched once (a package id is never reused)."""
    cache = _cache()
    kept = cache / f"bmv_{package['file']}" if cache else None
    if kept is not None and kept.exists():
        return kept.read_bytes()
    content = _get(f"{_BMV}/docs-pub/ifrsxbrl/{package['file']}")
    zipfile.ZipFile(io.BytesIO(content))  # a refusal page is no package
    if kept is not None:
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(content)
    return content


def cmf_statements(rut: str, end: date, scope: str = "C") -> tuple[bytes | None, date | None]:
    """A company's IFRS statements XBRL instance for a quarter (scope "C" consolidated, "I" the company alone) and the
    time of its latest submission; (None, None) where CMF has none for that quarter."""
    cache = _cache()
    stem = f"cmf_{rut}_{end:%Y%m}_{scope}"
    kept = cache / f"{stem}.json" if cache else None
    if kept is not None and kept.exists() and not _fresh(end.year):
        return _kept_cmf(cache, stem)
    try:
        instance, filed = _cmf_fetch(rut, end, scope)
    except OSError:
        if kept is None or not kept.exists():
            raise
        KEPT_SERVED.append(kept.name)  # CMF unreachable: the last copy kept, not none
        return _kept_cmf(cache, stem)
    if kept is not None:
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text(json.dumps({"filed": filed.isoformat() if filed else None}))
        if instance is not None:
            (cache / f"{stem}.xbrl").write_bytes(instance)
    return instance, filed


def _kept_cmf(cache, stem: str) -> tuple[bytes | None, date | None]:
    meta = json.loads((cache / f"{stem}.json").read_text())
    body = (cache / f"{stem}.xbrl").read_bytes() if meta.get("filed") else None
    return body, date.fromisoformat(meta["filed"]) if meta.get("filed") else None


def _cmf_fetch(rut: str, end: date, scope: str) -> tuple[bytes | None, date | None]:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    page_url = (f"{_CMF}entidad.php?mercado=V&rut={rut}&grupo=&tipoentidad=RVEMI&row=&vig=VI&control=svs&pestania=3")
    query = urllib.parse.urlencode({"forma": "P", "mm": f"{end.month:02d}", "aa": str(end.year), "tipo": scope,
                                    "tipo_norma": "IFRS"}).encode()
    page = _open(opener, urllib.request.Request(page_url, data=query, headers=_AGENT)).decode("latin-1", "replace")
    instance, filed = None, None
    for href, label in re.findall(r'href="(\.\./inc/inf_financiera/ifrs/safec_ifrs_verarchivo\.php[^"]+)"[^>]*>(.*?)</a>', page, re.S):
        if "XBRL" in label:
            content = _open(opener, urllib.request.Request(urllib.parse.urljoin(page_url, html.unescape(href)), headers=_AGENT))
            if content[:2] == b"PK":
                archive = zipfile.ZipFile(io.BytesIO(content))
                names = sorted(archive.namelist(), key=lambda name: not name.endswith(".xbrl"))  # the .xbrl, not its linkbases
                instance = next(body for body in (archive.read(name) for name in names if name.endswith((".xbrl", ".xml")))
                                if b"<xbrl" in body[:3000] or b":xbrl" in body[:3000])
            elif b"<xbrl" in content[:3000] or b":xbrl" in content[:3000]:
                instance = content  # CMF's 2010-11 statements come as the instance itself, not zipped
            else:
                raise ValueError(f"CMF's XBRL is neither a zip nor an instance: {content[:60]!r}")
    log = re.search(r"ventana_modal\('([^']+bitacora_ifrs\.php[^' ]+)", page)
    if instance is not None and log:
        text = _open(opener, urllib.request.Request(urllib.parse.urljoin(page_url, html.unescape(log.group(1))),
                                                   headers=_AGENT)).decode("latin-1", "replace")
        # Each submission's time, to the second ("18/11/2025 23:13:07 Primer Envio"); the page's own "Fecha de
        # consulta" carries no seconds. The XBRL served is the latest submission's.
        stamps = [datetime.strptime(stamp, "%d/%m/%Y") for stamp in re.findall(r"(\d{2}/\d{2}/\d{4}) \d{2}:\d{2}:\d{2}", text)]
        filed = max(stamps).date() if stamps else None
    if filed is None:
        instance = None  # a filing whose submission time CMF does not log is not read: its date would be a guess
    return instance, filed


def _open(opener, request, tries: int = 4) -> bytes:
    tries = 1 if "cmfchile.cl" in DOWN else tries
    for attempt in range(tries):
        try:
            time.sleep(1.0)  # one request a second: CMF is a public service, not a bulk endpoint
            with opener.open(request, timeout=120) as response:
                return response.read()
        except OSError:
            if attempt == tries - 1:
                DOWN.add("cmfchile.cl")
                raise
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("unreachable")
