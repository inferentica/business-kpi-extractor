"""Which of a filing's documents hold its printed statements: the report itself and an annual report exhibit."""
from __future__ import annotations


PRINTED_DOCUMENTS = ("10-K", "10-K405", "10-Q", "20-F", "40-F", "10-KT", "EX-13")


def release_documents(attachments) -> list:
    """A results release's furnished exhibits (EX-99.1, EX-99.2 …); a 6-K with none carries its content in its own
    document (TSMC's until October 2019)."""
    attachments = list(attachments)
    exhibits = [item for item in attachments if str(item.document_type or "").upper().startswith("EX-99")]
    return exhibits or [item for item in attachments if str(item.document_type or "").upper() == "6-K"][:1]
