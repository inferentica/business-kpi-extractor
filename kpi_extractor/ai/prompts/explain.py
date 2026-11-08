"""The task that decides whether a sharp change is real."""

from __future__ import annotations



EXPLAIN_TASK = """The values below changed sharply from the previous quarter. For each, decide from the document \
whether the change is real (for example a reorganisation, acquisition, divestiture, restatement, stock split, a new \
product ramp, or a change of definition the company states). If real, quote the exact sentence that shows it \
(max 200 characters, copied character for character). The sentence must be about this KPI (name it or state its \
figure) and this quarter: never guidance or an outlook, never another period's figure, never another line's. If the \
document gives no such reason, answer false. A sentence that only states the figure, or its change from a year ago, is \
not a reason; nor is a cause too small for the change from the previous figure shown (30,000 new hires do not double \
a headcount of 400,000).

JSON shape: {"items": {"<group>.<kpi>": {"legitimate": true, "quote": "..."}}}"""
