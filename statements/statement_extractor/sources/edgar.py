"""The one place this package reaches into the KPI extractor: EDGAR access and fiscal labels.

Those modules belong to the KPI extractor and move as it is restructured; when one moves, only these imports change.
Nothing here may import the KPI extractor's AI, readers or pipeline (tests/test_isolation.py enforces it).
"""
from __future__ import annotations


from kpi_extractor.fiscal import FiscalCalendarError, fiscal_label, learn_year_offset
from kpi_extractor.sec import (
    PERIODIC_FORMS,
    CompanyProfile,
    FilingRef,
    company_profile,
    configure_identity,
    load_submission,
    periodic_reports,
)

__all__ = [
    "PERIODIC_FORMS", "CompanyProfile", "FilingRef", "FiscalCalendarError", "company_profile", "configure_identity",
    "fiscal_label", "learn_year_offset", "load_submission", "periodic_reports",
]
