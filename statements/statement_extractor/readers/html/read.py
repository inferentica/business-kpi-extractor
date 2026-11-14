"""The three statements from a filing printed in HTML (10-K, 10-Q, 20-F of about 2001-2011)."""
from __future__ import annotations

from datetime import date

from ...domain.model import Reading
from ..printed.read import read_tables
from .parse import parse_html


def read_document(html: str | bytes, *, accession: str, form: str, filed: date, document_end: date | None,
                  fiscal_year_end: str, year_offset: int, unstated_scale: float | None = None,
                  left_out: frozenset = frozenset()) -> tuple[list[Reading], list[str]]:
    return read_tables(parse_html(html), accession=accession, form=form, filed=filed, document_end=document_end,
                       fiscal_year_end=fiscal_year_end, year_offset=year_offset,
                       unstated_scale=unstated_scale, left_out=left_out)
