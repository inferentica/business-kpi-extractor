"""Releases that name no statement, remembered between runs so they are not fetched again. Most 6-Ks a foreign filer
furnishes are no results release (Petrobras: 3,965 of 4,330 print nothing; notices, monthly sales, bond terms), and
fetching each full submission is most of a round's time.

A release is remembered only when none of its documents names a statement in any words the finder reads titles in
(readers/printed/find.py's TITLE_PATTERNS), so a release remembered under one version of those patterns stays empty
whatever else the readers learn. The list is keyed by the patterns themselves: change them and every release is read
again. A PDF document is never judged by its bytes, so a release with one is never remembered."""
from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path

from ..readers.printed.find import TITLE_PATTERNS

# Bump when releases are read for more than their text (reading image-only reports by OCR would make Copel's empty).
EMPTY_RULES = 1
_TAG = re.compile(r"<[^>]+>")


def _key() -> str:
    patterns = "|".join(pattern.pattern for pattern in TITLE_PATTERNS)
    return hashlib.sha1(f"{EMPTY_RULES}:{patterns}".encode()).hexdigest()[:12]


def _file(folder: str | None) -> Path | None:
    return Path(folder, f"empty-releases-{_key()}.json") if folder else None


def remembered(folder: str | None) -> set[str]:
    """Accessions of releases an earlier run found naming no statement, under the same title patterns."""
    path = _file(folder)
    try:
        return set(json.loads(path.read_text())) if path is not None and path.exists() else set()
    except (OSError, ValueError):
        return set()


def remember(folder: str | None, accessions: set[str]) -> None:
    """Keep this run's empty releases; lists under other title patterns are dropped."""
    path = _file(folder)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    for old in path.parent.glob("empty-releases-*.json"):
        if old != path:
            old.unlink(missing_ok=True)
    path.write_text(json.dumps(sorted(accessions)))


def names_a_statement(attachments) -> bool:
    """Whether any document could hold a statement: a statement title's words in its text, tags read as spaces or as
    nothing ("Balance<br>Sheet", "Bal<span>ance</span> Sheet"), or a PDF, whose words its bytes do not show."""
    for attachment in attachments:
        name = str(getattr(attachment, "document", "") or "").lower()
        if name.endswith(".pdf"):
            return True
        if name.endswith((".jpg", ".jpeg", ".gif", ".png", ".xml", ".xsd", ".zip")):
            continue
        content = attachment.content
        text = content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content or "")
        for joined in (_TAG.sub(" ", text), _TAG.sub("", text)):
            plain = re.sub(r"\s+", " ", html.unescape(joined).replace("\xa0", " "))
            if any(pattern.search(plain) for pattern in TITLE_PATTERNS):
                return True
    return False
