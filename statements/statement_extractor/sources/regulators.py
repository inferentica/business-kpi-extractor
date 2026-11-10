"""Home regulators' own data: the statements a foreign filer files with its home regulator, as that regulator publishes
them. Brazil's CVM publishes every listed company's quarterly (ITR, from 2011) and annual (DFP, from 2010) forms as
yearly CSV files; Korea's FSS publishes every line of each periodic report through OpenDART (years from 2015, quarters
from 2016). These are the companies' own filings to their regulators, never their websites.

Both are read on the runners: CVM's site refuses some networks outright, and OpenDART needs the DART_API_KEY secret.
Every file and answer is kept in the run's cache (STATEMENTS_ARCHIVE_CACHE) so a period already filed is not fetched
again; the current and previous year are fetched each run, as a late filing or an amendment lands there."""
from __future__ import annotations

import csv
import io
import json
import os
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

# Each company's identity at its regulator. Copel files with CVM as code 014311; TIM Brasil as TIM S.A. (from its 2020
# merger) and before that TIM Participações. KEPCO is DART corporation 00159193. Petrobras files with CVM too, in reais,
# but its SEC statements are in US dollars, so CVM's would be translations by our rule: not read.
REGULATED = {
    "ELPC": ("cvm", ("76.483.817/0001-20",)),
    "TIMB": ("cvm", ("02.421.421/0001-11", "02.558.115/0001-21")),
    "KEP": ("dart", ("00159193",)),
}

_CVM = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC"
_DART = "https://opendart.fss.or.kr/api"
_AGENT = "TradeTracker statements (admin@inferentica.com)"
# A CVM file's statements: balance sheet assets and liabilities, income, and cash flows by the indirect or direct method.
CVM_PARTS = ("BPA", "BPP", "DRE", "DFC_MI", "DFC_MD")
DART_REPORTS = ("11013", "11012", "11014", "11011")  # Q1, half-year, Q3, annual


def _cache() -> Path | None:
    folder = os.environ.get("STATEMENTS_ARCHIVE_CACHE")
    return Path(folder, "regulators") if folder else None


def _fresh(year: int) -> bool:
    """Whether a year's data may still change: this year's and last year's filings are still arriving."""
    return year >= date.today().year - 1


def _get(url: str, tries: int = 4) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _AGENT})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except OSError:
            if attempt == tries - 1:
                raise
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("unreachable")


def cvm_rows(form: str, year: int, cnpjs: tuple[str, ...]) -> dict[str, list[dict]]:
    """A company's rows of one year's CVM file (form "ITR" or "DFP"): the filing index ("index", one row per filing
    and version) and each statement part's rows, consolidated ("_con") and the parent's ("_ind")."""
    cache = _cache()
    kept = cache / f"cvm_{form}_{year}_{'_'.join(c[:10].replace('.', '') for c in cnpjs)}.json" if cache else None
    if kept is not None and kept.exists() and not _fresh(year):
        return json.loads(kept.read_text())
    name = f"{form.lower()}_cia_aberta_{year}"
    archive = zipfile.ZipFile(io.BytesIO(_get(f"{_CVM}/{form}/DADOS/{name}.zip")))
    wanted = {"index": f"{name}.csv"} | {f"{part}_{scope}": f"{name.replace('_cia_aberta', '_cia_aberta_' + part + '_' + scope)}.csv"
                                          for part in CVM_PARTS for scope in ("con", "ind")}
    found: dict[str, list[dict]] = {}
    members = set(archive.namelist())
    for key, member in wanted.items():
        if member not in members:
            continue
        text = archive.read(member).decode("latin-1")
        found[key] = [row for row in csv.DictReader(io.StringIO(text), delimiter=";") if row.get("CNPJ_CIA") in cnpjs]
    if kept is not None:
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text(json.dumps(found, ensure_ascii=False))
    return found


def dart_rows(corp_code: str, year: int, report: str, scope: str = "CFS") -> list[dict]:
    """Every line of one periodic report's statements from OpenDART (fnlttSinglAcntAll): group ("CFS") or the
    company alone ("OFS"). Empty where the report is not filed (status 013)."""
    cache = _cache()
    kept = cache / f"dart_{corp_code}_{year}_{report}_{scope}.json" if cache else None
    if kept is not None and kept.exists() and not _fresh(year):
        return json.loads(kept.read_text())
    key = os.environ.get("DART_API_KEY")
    if not key:
        raise RuntimeError("DART_API_KEY is not set")
    query = urllib.parse.urlencode({"crtfc_key": key, "corp_code": corp_code, "bsns_year": year,
                                    "reprt_code": report, "fs_div": scope})
    answer = json.loads(_get(f"{_DART}/fnlttSinglAcntAll.json?{query}"))
    if answer.get("status") not in ("000", "013"):
        raise RuntimeError(f"OpenDART {answer.get('status')}: {answer.get('message')}")
    rows = answer.get("list") or []
    if kept is not None:
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text(json.dumps(rows, ensure_ascii=False))
    return rows
