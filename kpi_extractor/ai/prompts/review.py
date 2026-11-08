"""The task that settles two readings that disagree."""

from __future__ import annotations



REVIEW_TASK = """Two independent readings of this document disagree on the KPIs below. For each, decide which reading \
is the value for the reported quarter, checking the document yourself. Answer "A" or "B", or "none" if neither is the \
reported quarter's value for that KPI (a prior-year column, a year-to-date figure, a different metric such as an \
app's DAUs for a family's DAP, or a KPI the document does not report).

For each KPI also give in "reasons" why the reading you turned down (both, for "none") is not the quarter's value: \
"prior_period" (a prior-year or prior-quarter column), "year_to_date", "full_year", "guidance" (an outlook), \
"different_metric" (a related measure under another name), "wrong_row" (another line of the same table), "wrong_scale" \
(the right figure in the wrong unit), or "not_reported" (that reading found nothing, or the document does not report it).

JSON shape: {"choices": {"<group>.<kpi>": "A"}, "reasons": {"<group>.<kpi>": "prior_period"}}"""
