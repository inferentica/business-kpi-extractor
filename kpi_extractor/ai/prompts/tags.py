"""The task that picks a quarter's tagged figures for the listed KPIs."""

from __future__ import annotations



TAG_TASK = """The document lists every figure the company tagged in its report for one quarter, one per line: \
[id] label [standard concept name] | the segment, product or other member it is tagged for | value | unit.
For each KPI below, give the id of the line that is exactly that KPI for the quarter, or null when no line is. A KPI \
named for part of the company (a segment or product, such as a cloud business) is the line tagged with that member; \
a KPI for the whole company is a line with no member, or with only a timing note ("expected from" a date). A company's own label may name the same thing differently \
(revenue backlog is the remaining performance obligation, headcount is the number of employees). Never choose a line \
that is only close: a different period, a part for a whole, or a component of the KPI is null.

JSON shape: {"picks": {"<kpi key>": "F12", "<other key>": null}}"""
