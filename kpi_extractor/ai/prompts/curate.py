"""The task that sorts an XBRL breakdown that does not add up to revenue."""

from __future__ import annotations



CURATE_TASK = """An XBRL revenue breakdown does not add up to reported revenue as tagged: it may include a parent row \
together with its children, a detail row that overlaps another (e.g. "U.S." beside "U.S. and Canada"), or rows from \
a different split. Choose the member keys that together make up total revenue exactly once, with no overlaps. Return \
an empty list if no subset is a genuine breakdown of total revenue.

JSON shape: {"members": ["us_and_canada", "europe", "asia_pacific", "rest_of_world"]}"""
