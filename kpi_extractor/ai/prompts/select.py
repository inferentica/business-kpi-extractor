"""The task that chooses the tables to read from a long document's outline."""

from __future__ import annotations



SELECT_TASK = """This document is too long to read whole. From its outline, choose the ids of every table and text \
block that could hold the KPIs listed below or a breakdown of revenue (by segment, product, platform, technology, \
geography), and the passage naming the reporting period. Prefer too many over too few; leave out legal notes, \
leases, debt, tax, pensions, equity and related-party tables.

JSON shape: {"ids": ["A_T12", "A_P3"]}"""
