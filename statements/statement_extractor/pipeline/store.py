"""Writes one company's read to Quotiva through its statements-store gate (Phase 7, s7-writes): the runner holds no key.
With the run's GitHub OIDC token (the gate takes main's statements workflow only) it asks for links to write the
evidence under statements/v1/<cik>/ in R2, writes it, then asks the gate to serve the snapshot it wrote there.

    statement-extractor store out/AAPL [--dry-run]

The evidence (statements/docs/storage.md): the read's units (units/<unit>.json.gz with units/index.json.gz, s7-
incremental; a read without units gives each filing's readings, readings/<accession>.json.gz), the filings read
(filings.json.gz), the served snapshot (served.json.gz) and the run's summary (summary.json.gz), gzipped with no
time stamp so a file read alike is written alike. A dry run writes only its snapshot, as candidate.json.gz: what R2
holds as served stays what Quotiva serves.

The next read of the company builds on those units (`fetch_units`): the gate signs links to read them, for main's
statements workflow only."""
from __future__ import annotations

import gzip
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

URL = os.environ.get("STATEMENTS_STORE_URL", "https://gplrniwqomrrufkkxqjh.supabase.co/functions/v1/statements-store")
AUDIENCE = "quotiva-statements"
_BATCH = 500  # links asked for at once (the gate signs up to 1,000)


def _packed(value) -> bytes:
    return gzip.compress(json.dumps(value, default=str, separators=(",", ":")).encode(), mtime=0)


def evidence_name(accession: str) -> str:
    """A filing's evidence key: its accession, an archived report's colons as hyphens (archive-TSM-20-F-2000-12-31)."""
    return f"readings/{re.sub(r'[^A-Za-z0-9._-]', '-', accession)}.json.gz"


def evidence(folder: Path) -> dict[str, bytes]:
    units = folder / "units"
    if (units / "index.json").exists():
        files = {f"units/{path.name}": path.read_bytes() for path in sorted(units.glob("*.json.gz"))}
        files["units/index.json.gz"] = _packed(json.loads((units / "index.json").read_text()))
    else:
        by_filing: dict[str, list] = defaultdict(list)
        with (folder / "readings.jsonl").open() as handle:
            for line in handle:
                if line.strip():
                    reading = json.loads(line)
                    by_filing[evidence_name(reading["accession"])].append(reading)
        files = {name: _packed(readings) for name, readings in sorted(by_filing.items())}
    for name in ("filings", "served", "summary"):
        files[f"{name}.json.gz"] = _packed(json.loads((folder / f"{name}.json").read_text()))
    return files


def store(folder: Path, dry_run: bool = False, allow_withdraw: bool = False, post=None, put=None) -> dict:
    post, put = post or _post, put or _put
    cik = json.loads((folder / "served.json").read_text()).get("cik")
    if not cik:
        raise RuntimeError(f"{folder}: served.json names no CIK")
    files = evidence(folder)
    if dry_run:
        files = {"candidate.json.gz": files["served.json.gz"]}
    # The snapshot last, so the gate never serves a snapshot whose evidence is not written yet.
    names = sorted(files, key=lambda name: name == "served.json.gz")
    for start in range(0, len(names), _BATCH):
        batch = names[start:start + _BATCH]
        links = post({"operation": "uploads", "cik": cik,
                      "files": [{"name": name, "size": len(files[name])} for name in batch]})["links"]
        for name in batch:
            put(links[name], files[name])
    result = post({"operation": "store", "cik": cik, "dry_run": dry_run, "allow_withdraw": allow_withdraw})
    return {**result, "files": len(files), "bytes": sum(len(body) for body in files.values())}


def fetch_units(folder: Path, cik: int, post=None, get=None) -> int:
    """The company's units as its last stored read left them, into `folder`/units for `read --previous`; how many."""
    post, get = post or _post, get or _get
    links = post({"operation": "downloads", "cik": cik})["links"]
    units = folder / "units"
    units.mkdir(parents=True, exist_ok=True)
    count = 0
    for name, link in sorted(links.items()):
        if name == "units/index.json.gz":
            (units / "index.json").write_bytes(gzip.decompress(get(link)))
        elif re.fullmatch(r"units/[A-Za-z0-9][A-Za-z0-9._-]*\.json\.gz", name):
            (units / name.split("/", 1)[1]).write_bytes(get(link))
            count += 1
    return count


def _get(link: str) -> bytes:
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(link, timeout=120) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 3:
                raise RuntimeError(f"unit download failed: {error}") from error
        time.sleep(5 * attempt)
    raise RuntimeError("unit download failed")


def _post(body: dict) -> dict:
    data = json.dumps(body).encode()
    for attempt in range(1, 4):
        request = urllib.request.Request(URL, data=data, method="POST", headers={
            "Authorization": f"Bearer {_oidc_token()}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:500]
            if error.code < 500 or attempt == 3:  # a refusal is final: the snapshot or the token is wrong
                raise RuntimeError(f"statements-store refused ({error.code}): {detail}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 3:
                raise RuntimeError(f"statements-store failed: {error}") from error
        time.sleep(10 * attempt)
    raise RuntimeError("statements-store failed")


def _put(link: str, body: bytes) -> None:
    for attempt in range(1, 4):
        request = urllib.request.Request(link, data=body, method="PUT", headers={
            "Content-Type": "application/gzip", "Content-Length": str(len(body))})
        try:
            with urllib.request.urlopen(request, timeout=120):
                return
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 3:
                raise RuntimeError(f"evidence upload failed: {error}") from error
        time.sleep(5 * attempt)


def _oidc_token() -> str:
    request_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not request_url or not request_token:
        raise RuntimeError("GitHub OIDC is unavailable; run inside GitHub Actions with id-token: write")
    separator = "&" if "?" in request_url else "?"
    request = urllib.request.Request(f"{request_url}{separator}audience={urllib.parse.quote(AUDIENCE)}",
                                     headers={"Authorization": f"Bearer {request_token}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())["value"]
