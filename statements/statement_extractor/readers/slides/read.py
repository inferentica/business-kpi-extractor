"""The three statements from a release printed as slide images with their words hidden beneath (ASML from 2025)."""
from __future__ import annotations

from datetime import date

from ...domain.model import Reading
from ..printed.read import read_tables
from .parse import parse_slides


def read_document(content: str, *, accession: str, form: str, filed: date, document_end: date | None,
                  fiscal_year_end: str, year_offset: int) -> tuple[list[Reading], list[str]]:
    return read_tables(parse_slides(content), accession=accession, form=form, filed=filed, document_end=document_end,
                       fiscal_year_end=fiscal_year_end, year_offset=year_offset)
