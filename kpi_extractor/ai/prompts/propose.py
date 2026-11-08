"""The task that proposes a company's KPI list."""

from __future__ import annotations

from .common import DOCUMENT_FORMAT
from ..schemas import MAX_GROUPS, MAX_KPIS_PER_GROUP, MAX_LISTED_KPIS


PROPOSE_TASK = f"""Choose the business KPIs worth tracking every quarter for this company, from its latest earnings \
documents. {DOCUMENT_FORMAT}

Track:
- Revenue breakdowns reported as amounts: by segment, product line, platform, technology, customer type, geography.
- Percentage mixes that split revenue, rent or shipments, e.g. revenue by technology node or by platform, or a REIT's rent by tenant industry. Never overall growth or its parts (underlying, organic, volume or price growth for the whole company).
- Operating metrics that drive the business: users (DAU, MAU, DAP), paid members or subscribers, ARPU/ARPP, units \
shipped or deliveries, wafer shipments, backlog or remaining performance obligations, bookings, gross bookings/GMV, \
trips, same-store sales, store count, net revenue retention, ARR, ad impressions and price per ad growth.

Never track: total revenue on its own, revenue or profit growth rates (including FX-neutral or constant-currency \
growth), costs, margins, operating or net income, EPS, cash flow or balance sheet lines, content, purchase or lease \
obligations, non-GAAP adjustments, guidance or outlook, dividends and buybacks, or anything not given as a number for \
the reported quarter. Industry hints, when given after this task, lift this list only for the measures they name.

When the documents give a breakdown as amounts (for example a financial report's revenue by technology in NT$), track \
the amounts as a revenue_breakdown and do not also track its percentage shares: shares are computed from amounts. \
Track a percentage mix only when the company gives no amounts for it, and only when it states every share.
Do not track a breakdown that has the same rows as one listed under "Already covered by XBRL", even under another \
title.

Every group must be checkable: a revenue breakdown whose parts add up to a total row shown beside them, a mix whose shares \
add up to about 100, or operating metrics reported the same way each quarter. A breakdown's rows are parts of revenue \
or of one segment's revenue; never income-statement lines such as net interest income, interest expense or \
noninterest revenue listed together.

Rules:
- kind "revenue_breakdown": currency amounts that together make up revenue. Amounts that are not revenue (annualized rent, loans, deposits, client assets, backlog, bookings) are operating metrics, never a revenue_breakdown. Include the table's total row as a KPI and \
name it in "total_kpi", with at least two parts beside it; a single amount (e.g. AI revenue) is a metric, not a \
breakdown. Skip any breakdown listed under "Already covered by XBRL".
- kind "mix": percentages that add up to about 100.
- kind "metric": standalone operating metrics; put them all in one group keyed "operating", labelled "Operating Metrics".
- unit is one of currency, percent, count, ratio (a percentage growth rate is "percent").
- keys are short snake_case and stable.

Naming (for your groups and for "names") — concise, plain English a reader understands without the filing, and \
professional; use the company's official name only when it is itself clear:
- Breakdown titles read "Revenue by <Dimension>" in Title Case with a one- or two-word dimension: Segment, Product, \
Platform, Technology, Geography, Region, End Market, Customer. Never "Revenue by Client and Gaming Business" or \
"Revenue by Google Services Product": say "Revenue by Segment" or "Revenue by Product".
- Row labels use the company's own names and casing (iPhone, HPC, Family of Apps, 3nm), never XBRL words such as \
"Member", "Segment" suffixes the company does not use, or "srt"/"us-gaap" prefixes.
- A KPI tracked every quarter never names a year or a quarter: a measure the company restates for the year in progress \
is "Current-Year Contract Coverage", not "Full-Year 2026 Contract Coverage", which would be wrong in every other year.
- Metric labels are short Title Case nouns: "Employees", "Daily Active People (DAP)", "Paid Memberships", "AWS Revenue \
Run Rate", "Satellites in Orbit". Spell out internal shorthand (WW → Worldwide, Y/Y → Year over Year, 3P → \
Third-Party). Avoid the word "mix" unless it is the company's own term; describe a share plainly ("Third-Party Seller \
Share of Units"). Keep well-known abbreviations the company uses (AWS, DAP, ARPU) and add the full words in brackets \
only when they help.
- When a group you track is the same breakdown as one listed under "Name these XBRL breakdowns" (for example the \
quarterly version of an annual XBRL breakdown), give both the same title and row labels, so they read as one view.
- "names" renames each breakdown listed under "Name these XBRL breakdowns": {{"<breakdown key>": {{"label": "...", \
"members": {{"<member key>": "..."}}}}}}. Use only the keys given; omit a member whose label is already right.
- At most {MAX_GROUPS} groups and {MAX_KPIS_PER_GROUP} KPIs per group, and about {MAX_LISTED_KPIS} KPIs in all. Over that, \
operating metrics are dropped from the end of their group (breakdowns and mixes never are), so list operating metrics \
most important first. Prefer fewer, clearly reported KPIs.

JSON shape:
{{"groups": [{{"key": "revenue_by_technology", "label": "Revenue by Technology", "kind": "mix", "total_kpi": null,
  "kpis": [{{"key": "n3", "label": "3nm", "unit": "percent"}}]}}],
 "names": {{"products": {{"label": "Revenue by Market Platform", "members": {{"hyperscale": "Hyperscale"}}}}}}}}"""
