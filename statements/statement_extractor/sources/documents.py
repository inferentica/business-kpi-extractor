"""Which of a filing's documents hold its printed statements: the report itself and an annual report exhibit."""
from __future__ import annotations

import re


PRINTED_DOCUMENTS = ("10-K", "10-K405", "10-Q", "20-F", "40-F", "10-KT", "EX-13")


_NUMBERED = re.compile(r"^EX-\d+(\.\d+)?$")


def release_documents(attachments) -> list:
    """A results release's furnished exhibits (EX-99.1, EX-99.2 …); else its other numbered exhibits (Brookfield's 6-K
    of 24 March 2011 files its annual report as EX-1, the statements its 40-F incorporates by reference); a 6-K with
    none carries its content in its own document (TSMC's until October 2019)."""
    attachments = list(attachments)
    kinds = [(item, str(item.document_type or "").upper()) for item in attachments]
    exhibits = [item for item, kind in kinds if kind.startswith("EX-99")] or \
        [item for item, kind in kinds if _NUMBERED.match(kind) and not kind.startswith("EX-10")]
    return exhibits or [item for item, kind in kinds if kind == "6-K"][:1]
