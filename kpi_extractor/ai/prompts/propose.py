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
- Growth the company states for one named product, platform, user base, unit count or headcount (Vector growth, \
AI-first ARR growth, Daily Active People growth, paid units growth, headcount growth), as a percent. A product's \
revenue growth only when the company gives no amount for that revenue: growth is computed from amounts.

Never track: total revenue on its own, growth of total revenue, of a segment's revenue or of profit (including \
organic, FX-neutral or constant-currency growth), costs, margins, operating or net income, EPS, cash flow or balance sheet lines, content, purchase or lease \
obligations, non-GAAP adjustments, guidance or outlook, dividends and buybacks, or anything not given as a number for \
the reported quarter. Industry hints, when given after this task, lift this list only for the measures they name.
A monthly report (an insurer's monthly results) is read the same way: track only what it gives for the whole quarter, \
in a quarter or three-month column or as a count at the quarter's end, never a figure it gives only for the month or \
the year to date.

When the documents give a breakdown as amounts (for example a financial report's revenue by technology in NT$), track \
the amounts as a revenue_breakdown and do not also track its percentage shares: shares are computed from amounts. \
Track a percentage mix only when the company gives no amounts for it, and only when it states every share.
Do not track a breakdown whose rows have the same names as one listed under "Already covered by XBRL" (the same \
segments, products or regions, whatever its title). A table that splits revenue another way (strategic and \
non-strategic revenue beside the segments, or one segment's products) is not covered: track it.

Every group must be checkable: a revenue breakdown whose parts add up to a total row shown beside them, a mix whose shares \
add up to about 100, or operating metrics reported the same way each quarter. A breakdown's rows are parts of revenue \
or of one segment's revenue; never income-statement lines such as net interest income, interest expense or \
noninterest revenue listed together.

Rules:
- kind "revenue_breakdown": currency amounts that together make up revenue. Amounts that are not revenue (annualized rent, loans, deposits, client assets, backlog, bookings) are operating metrics, never a revenue_breakdown. Include the table's total row as a KPI and \
name it in "total_kpi", with at least two parts beside it; a single amount (e.g. AI revenue) is a metric, not a \
breakdown. Skip a breakdown whose rows are those of one under "Already covered by XBRL".
- kind "mix": percentages that add up to about 100.
- kind "metric": operating metrics. Standalone ones go in one group keyed "operating", labelled "Operating Metrics". A \
table that breaks one metric down (assets under management by asset class, loans or deposits by type, deliveries by \
program, premiums by line, subscribers by plan) is a metric group of its own, titled for what it breaks down ("AUM by \
Asset Class"), with the table's total row when it has one.
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
- "left_out" names up to 10 measures these documents give as a number for the quarter that a reader might want \
tracked but you chose not to, most notable first: {{"label": "...", "unit": "...", "reason": "...", "group": "<key of \
the group it would join, or null>"}}. Only breakdowns, operating metrics and a product's figures belong here, with \
the reason in two to five words ("covered by XBRL", "year to date only", "stated only in prose", "a one-off figure"). \
Never list what "Never track" names (margins, costs, EPS, income, cash flow, balance sheet, guidance, total revenue or \
its growth), never what you track, and never a measure the documents do not give.
- Track every measure these documents give for the quarter that these rules allow, at most {MAX_GROUPS} groups and \
{MAX_KPIS_PER_GROUP} KPIs per group, and about {MAX_LISTED_KPIS} KPIs in all. Over that, operating metrics are dropped \
from the end of their groups (breakdowns and mixes never are), so list each group's most important first.

JSON shape:
{{"groups": [{{"key": "revenue_by_technology", "label": "Revenue by Technology", "kind": "mix", "total_kpi": null,
  "kpis": [{{"key": "n3", "label": "3nm", "unit": "percent"}}]}}],
 "names": {{"products": {{"label": "Revenue by Market Platform", "members": {{"hyperscale": "Hyperscale"}}}}}},
 "left_out": [{{"label": "Year-to-Date Bookings", "unit": "currency", "reason": "year to date only", "group": "operating"}}]}}"""
