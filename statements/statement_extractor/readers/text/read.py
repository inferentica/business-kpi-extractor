"""The three statements from a filing printed as plain text (EDGAR's SGML filings of about 1993-2000)."""
from __future__ import annotations

from datetime import date

from ...domain.model import Reading
from ..printed.read import read_tables
from .parse import parse_text


def read_document(text: str | bytes, *, accession: str, form: str, filed: date, document_end: date | None,
                  fiscal_year_end: str, year_offset: int, unstated_scale: float | None = None,
                  left_out: frozenset = frozenset()) -> tuple[list[Reading], list[str]]:
    text = text.decode("utf-8", "replace") if isinstance(text, bytes) else text
    return read_tables(parse_text(text), accession=accession, form=form, filed=filed, document_end=document_end,
                       fiscal_year_end=fiscal_year_end, year_offset=year_offset,
                       unstated_scale=unstated_scale, left_out=left_out)
