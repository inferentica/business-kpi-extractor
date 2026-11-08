"""The task that audits a company's KPI list against its latest documents."""

from __future__ import annotations

from .common import DOCUMENT_FORMAT


MAINTAIN_TASK = f"""Audit this company's KPI list against its latest earnings documents. {DOCUMENT_FORMAT}

You are given the KPI list and the values captured from these documents. Report:
- "add_kpis": KPIs of an existing group that the documents report for the quarter but the list lacks (e.g. a new \
technology node or a new segment), keyed by group key.
- "add_groups": whole breakdowns, mixes or operating metrics the documents report for the quarter that no group covers \
and that follow the same rules as the list (no totals alone, costs, margins, profits, EPS, cash flow, guidance).
- "retire": "<group>.<kpi>" keys the company no longer reports at all (not merely missing from one table).
Keep keys short snake_case and never reuse an existing key. Name things as the existing list does. Return empty lists \
when nothing is missing.

JSON shape (every group needs key, label, kind and kpis; every KPI needs key, label and unit):
{{"add_groups": [{{"key": "operating", "label": "Operating Metrics", "kind": "metric", "kpis": [{{"key": "wafers", "label": "Wafer Shipments", "unit": "count"}}]}}],
 "add_kpis": {{"revenue_by_technology": [{{"key": "n2", "label": "2nm", "unit": "currency"}}]}}, "retire": []}}"""
