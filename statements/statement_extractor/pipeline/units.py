"""Read units (Phase 7, s7-incremental): what each source gave the read, kept so the next read of the company reads
only what is new. A unit is one EDGAR report (10-K, 10-Q, 20-F, 40-F, an XBRL 6-K), one 6-K release, one year of a
home regulator's form, or the printed second look at an XBRL filing. It holds the unit's readings exactly as its
source gave them, before anything looks across filings (currencies, bases, dates, the choice), with its filing record,
what else its read produced (summary figures, printed signs) and the context it was read in: the reader version, the
fiscal year end and year naming it was read with.

The next read reuses a unit only when the reader version and its context are the same as a full read would give it;
any other unit is read again. Everything that looks across filings then runs over all units' readings, reused and new
alike, from the same raw form, so an incremental read equals a full read of the same filings (statements/docs/
storage.md, "Incremental reads").

    out/<SYMBOL>/units/<unit>.json.gz    one unit
    out/<SYMBOL>/units/index.json        every unit: id, kind, version, context, filed, complete
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import re
from functools import lru_cache
from importlib import metadata, util
from pathlib import Path

from ..domain.model import Reading

PACKAGE = Path(__file__).resolve().parents[1]
# The KPI extractor's modules the reader reaches (sources/edgar.py) and the libraries that shape what it reads.
_BRIDGED = ("kpi_extractor.domain.fiscal", "kpi_extractor.sources.sec")
_LIBRARIES = ("edgartools", "lxml", "pdfplumber", "arelle-release", "pyarrow")


@lru_cache(maxsize=1)
def reader_version() -> str:
    """The reader's own version: a hash of the package's files, the KPI modules it reaches and its libraries' versions.
    Any change to them reads every unit again (a narrower rule per era is s7-versions)."""
    digest = hashlib.sha256()
    for path in sorted(PACKAGE.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            digest.update(str(path.relative_to(PACKAGE)).encode() + b"\0" + path.read_bytes() + b"\0")
    for name in _BRIDGED:
        spec = util.find_spec(name)
        digest.update(name.encode() + b"\0" + (Path(spec.origin).read_bytes() if spec and spec.origin else b"") + b"\0")
    for name in _LIBRARIES:
        try:
            digest.update(f"{name}={metadata.version(name)}\0".encode())
        except metadata.PackageNotFoundError:
            digest.update(f"{name}=none\0".encode())
    return digest.hexdigest()[:16]


def unit_id(kind: str, key: str) -> str:
    return f"{kind}-{re.sub(r'[^A-Za-z0-9._-]', '-', key)}"


def _strict(value):
    raise TypeError(f"a read unit holds only JSON values, not {type(value).__name__}")


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, default=_strict)


class Units:
    """The units a previous read left (`previous`, its units/ folder) and the ones this read keeps (`out`/units)."""

    def __init__(self, out: Path, previous: Path | None = None, drop_newest: int = 0):
        self.version = reader_version()
        self.folder = out / "units"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.previous = previous / "units" if previous and (previous / "units").is_dir() else None
        index = json.loads((self.previous / "index.json").read_text()) if self.previous and \
            (self.previous / "index.json").exists() else []
        self.stored = {entry["id"]: entry for entry in index}
        self.dropped = self._newest(drop_newest)
        self.index: list[dict] = []
        self.counts = {"reused": 0, "read": 0, "read again": 0, "new": 0}

    def _newest(self, count: int) -> set[str]:
        """The units of the `count` newest filings, set aside as if never read (the incremental round's test)."""
        filed = sorted({(entry["filed"], entry["accession"]) for entry in self.stored.values()
                        if entry["kind"] in ("report", "release") and entry.get("filed")}, reverse=True)[:count]
        newest = {accession for _filed, accession in filed}
        return {key for key, entry in self.stored.items() if entry.get("accession") in newest}

    def stored_extra(self, id: str, name: str):
        """A field a stored unit's read produced that does not depend on the reader (a report's period on EDGAR)."""
        entry = self.stored.get(id)
        return None if entry is None or id in self.dropped else (entry.get("facts") or {}).get(name)

    def reuse(self, id: str, context: dict) -> dict | None:
        """The stored unit, when this read would read it in the same context with the same reader; else None."""
        entry = self.stored.get(id)
        if entry is None or id in self.dropped:
            self.counts["new"] += 1
            return None
        if entry["version"] != self.version or not entry.get("complete", True) or \
                _canonical(entry["context"]) != _canonical(context):
            self.counts["read again"] += 1  # another context or reader, or unfinished (unreadable, Arelle's budget)
            return None
        unit = json.loads(gzip.decompress((self.previous / f"{id}.json.gz").read_bytes()))
        self.counts["reused"] += 1
        self._write(unit)
        return unit

    def keep(self, kind: str, key: str, context: dict, record: dict | None, readings: list[Reading],
             extra: dict | None = None, facts: dict | None = None, accession: str | None = None,
             filed: str | None = None, complete: bool = True) -> dict:
        """A unit just read, written before anything looks across filings; its readings come back fresh from it."""
        unit = {"id": unit_id(kind, key), "kind": kind, "version": self.version, "context": context,
                "accession": accession, "filed": filed, "complete": complete, "facts": facts or {},
                "record": copy.deepcopy(record), "extra": copy.deepcopy(extra or {}),
                "readings": [reading.to_raw() for reading in readings]}
        unit = json.loads(_canonical(unit))  # what a later read loads, so both reads go on from the same values
        self.counts["read"] += 1
        self._write(unit)
        return unit

    def _write(self, unit: dict) -> None:
        (self.folder / f"{unit['id']}.json.gz").write_bytes(
            gzip.compress(json.dumps(unit, sort_keys=True, separators=(",", ":")).encode(), mtime=0))
        self.index.append({field: unit[field] for field in (
            "id", "kind", "version", "context", "accession", "filed", "complete", "facts")})

    def finish(self) -> dict:
        (self.folder / "index.json").write_text(json.dumps(sorted(self.index, key=lambda entry: entry["id"]), indent=0))
        return {"reader_version": self.version, "previous": bool(self.previous), "dropped": len(self.dropped),
                **self.counts}


def readings_of(unit: dict) -> list[Reading]:
    return [Reading.from_raw(reading) for reading in unit["readings"]]


def record_of(unit: dict) -> dict | None:
    return copy.deepcopy(unit["record"])
