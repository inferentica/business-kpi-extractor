"""The task that points to where each KPI's value sits for the quarter."""

from __future__ import annotations

from .common import DOCUMENT_FORMAT


LOCATE_TASK = f"""Find where each KPI below has its value for the reported quarter. {DOCUMENT_FORMAT}

For a revenue_breakdown or mix laid out as one table, point to the table once instead of to each row:
  {{"group": "<group>", "table": "B_T68", "col": 1, "first_row": 2, "last_row": 12, "total_row": 13, "scale": 1000, \
"first_label": "North America", "last_label": "Other"}}
  Code reads every row from first_row to last_row in that column, including rows not in the KPI list (a new node, a \
regrouped region), so include every row of the breakdown and nothing else; total_row is the breakdown's total (null if \
none). Use this in "tables".

For each other KPI, in "values", either:
- a table cell: {{"kpi": "<group>.<kpi>", "table": "A_T3", "row": 5, "col": 2, "scale": 1000000, "row_label": "Wafer revenue"}}
  "row_label" (and a table's "first_label" and "last_label") is the row's own label copied from its c0 cell, not the \
KPI's name; code checks it against the row you point to. With every "scale" other than 1, give "scale_text": the \
document's own words that state it, copied exactly ("in millions", "($ millions)", "(Bil.)"); a scale the document \
does not state is not used.
  "scale" is the multiplier that applies to that row ("in millions" = 1000000, "in thousands" = 1000, none = 1). A \
header such as "(in millions, except employee data)" does not apply to the rows it excepts: use 1 for them.
- or text: {{"kpi": "<group>.<kpi>", "block": "A_P7", "quote": "<exact excerpt, max 160 chars>", "value_text": "3.60 billion"}}
  The quote is copied character for character from that block and contains value_text, the number exactly as written \
with its unit word or % sign. Ids containing P are text blocks: quote them; only ids containing T are tables. A table \
whose cells are sentences (highlights set as bullets) may be quoted the same way, with its id as "block".

Rules:
- Use the reported quarter's column (three months ended at the period end, or the 12, 13, 14 or 16 weeks ended then \
for a company whose quarters are counted in weeks), never the prior-year quarter, the prior quarter, year-to-date \
or full-year columns.
- For a percentage, point to the percentage itself, not an amount.
- If a KPI is not reported for this quarter, list its key in "missing". Do not guess. Give each missing key a reason in \
"missing_reasons": "not_reported" (the document does not report it), "elsewhere" (the document says it is given in \
another document, a presentation or an attachment), "related_only" (only a related measure under another name is \
reported), or "guidance_only" (only an outlook or forecast mentions it).
- Never point to guidance, an outlook or any statement of what the company expects ("We expect revenue from AI to be \
$12 billion for fiscal year 2024"): only to what it reports for the period.
- The value must be the measure the KPI names, not a related one under another name: one app's daily active users \
(DAUs) are not a family's daily active people (DAP), bookings are not revenue. List such a KPI in "missing".
- "period_end" is the last day of the reported quarter as YYYY-MM-DD.
- If the document reports only full-year figures for the period (an annual report with no three-month column), use \
the full-year column, set "annual": true, and use the fiscal year end as "period_end".

JSON shape: {{"period_end": "2026-06-30", "annual": false, "tables": [...], "values": [...], "missing": ["operating.dap"], \
"missing_reasons": {{"operating.dap": "related_only"}}}}"""
