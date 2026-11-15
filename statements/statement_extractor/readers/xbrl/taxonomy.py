"""The taxonomies a filing's XBRL is tagged in, with their years, from the schema it files: Apple's FY2024 10-K imports
`http://fasb.org/us-gaap/2024`, an IFRS filer `http://xbrl.ifrs.org/taxonomy/2023-03-23/ifrs-full`. The SEC's data sets
record a tag with its taxonomy's version; a line's taxonomy is `us-gaap/2024` (domain/line_fields.py)."""
from __future__ import annotations

import re

from ...domain.line_fields import taxonomy_of

_SEC = re.compile(r"(?:fasb\.org|xbrl\.sec\.gov|xbrl\.us)/(us-gaap|srt|dei)/(\d{4})")
_IFRS = re.compile(r"xbrl\.ifrs\.org/taxonomy/(\d{4})-\d{2}-\d{2}/(ifrs-full)")


def versions_in(schema: str) -> dict[str, str]:
    """Each standard taxonomy prefix a schema imports, with its year: {"us-gaap": "us-gaap/2024"}."""
    found = {prefix: f"{prefix}/{year}" for prefix, year in _SEC.findall(schema)}
    found.update({prefix: f"{prefix}/{year}" for year, prefix in _IFRS.findall(schema)})
    return found


def taxonomy_versions(filing) -> dict[str, str]:
    """The filing's taxonomy years, or none where its schema cannot be read (a line keeps its prefix alone)."""
    try:
        from edgar.xbrl.xbrl import XBRLAttachments
        schema = XBRLAttachments(filing.attachments).get("schema")
        return versions_in(str(schema.content)) if schema is not None else {}
    except Exception:  # noqa: BLE001 - the taxonomy's year is a label of the tag, never a reason to lose a filing
        return {}


def tag_taxonomies(readings: list, versions: dict[str, str]) -> None:
    for reading in readings:
        for line in reading.lines:
            if not line.get("is_heading"):
                line["taxonomy"] = taxonomy_of(str(line.get("concept") or ""), versions)
