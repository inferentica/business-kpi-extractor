"""Owner-built lists (business_kpi_specs.model = 'owner'): a new filing fills only its new period into the lines the owner
built, by code from each line's stored source, never by AI. Existing periods are never touched (owner, 2026-11-25)."""
from .sources import GroupMapping, LineMapping, OwnerSources, TableSource, XbrlSource, parse_sources

__all__ = ["GroupMapping", "LineMapping", "OwnerSources", "TableSource", "XbrlSource", "parse_sources"]
