"""What every prompt shares: the system message, the document's format, and the prompt's layout (document first, so
repeated tasks on one document hit the context cache)."""

from __future__ import annotations



SYSTEM = """You analyse SEC filings for a financial data pipeline. You never write a number that will be stored: you \
point to where a value is (a table cell, or an exact quote with the number as written) or you choose among options \
given to you, and code reads and checks every value. Reply with a single JSON object and nothing else."""


def prompt(company: str, document: str, task: str, data: str = "") -> str:
    """Company, then the document, then the task: everything before the task is shared by every task on a document."""
    return f"Company: {company}\n\n=== DOCUMENT ===\n{document}\n\n=== TASK ===\n{task}" + (f"\n\n{data}" if data else "")


DOCUMENT_FORMAT = """The document is split into tables [T..] with rows r<n> and columns c<n>, and text blocks [P..]. \
Each exhibit's ids carry its own prefix (A_, B_, ...)."""
