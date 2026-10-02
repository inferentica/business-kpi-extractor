"""Every AI task, its prompt and the schema its answer must pass.

The AI never supplies a number that gets stored. It chooses (which documents, sections, KPIs and rows matter) and it
points (a table cell, or a quote with the number as written); code reads every value from the document and checks it.

Every prompt shares one system message and puts the document first, so repeated tasks on the same document (Pro's
reading, review, audit and explanations) hit DeepSeek's context cache and cost a fraction of a fresh read.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

GroupKind = Literal["revenue_breakdown", "mix", "metric"]
Unit = Literal["currency", "percent", "count", "ratio"]
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,47}$")

MAX_GROUPS = 8
MAX_KPIS_PER_GROUP = 16
# A focused list: every KPI is read, and possibly disputed, every quarter. The proposal is asked for at most six
# groups; the code keeps any list to this many KPIs (see normalize_spec).
MAX_LISTED_KPIS = 24
MIN_KEPT_METRICS = 4

SYSTEM = """You analyse SEC filings for a financial data pipeline. You never write a number that will be stored: you \
point to where a value is (a table cell, or an exact quote with the number as written) or you choose among options \
given to you, and code reads and checks every value. Reply with a single JSON object and nothing else."""


def _check_key(value: str) -> str:
    # A key the AI made too long or wrote loosely ("global_corporate_banking_global_investment_banking") is repaired
    # rather than costing the whole list: lower case, underscores, at most 48 characters, starting with a letter.
    repaired = re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", str(value).lower())).strip("_")[:48].rstrip("_")
    if repaired and not repaired[0].isalpha():
        repaired = f"k_{repaired}"[:48].rstrip("_")
    if not _KEY.match(repaired):
        raise ValueError(f"invalid key {value!r}")
    return repaired


class KpiSpec(BaseModel):
    key: str
    label: str = Field(min_length=1, max_length=80)
    unit: Unit

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        return _check_key(value)


class GroupSpec(BaseModel):
    key: str
    label: str = Field(min_length=1, max_length=80)
    kind: GroupKind
    total_kpi: str | None = None
    kpis: list[KpiSpec] = Field(min_length=1, max_length=MAX_KPIS_PER_GROUP)

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        return _check_key(value)

    @model_validator(mode="after")
    def _consistent(self) -> "GroupSpec":
        keys = [kpi.key for kpi in self.kpis]
        if len(set(keys)) != len(keys):
            raise ValueError(f"duplicate KPI keys in {self.key}")
        if self.kind == "revenue_breakdown":
            if any(kpi.unit != "currency" for kpi in self.kpis):
                raise ValueError(f"{self.key}: revenue breakdowns hold currency amounts")
            if self.total_kpi is not None and self.total_kpi not in keys:
                # A total pointing at no KPI of its own group is dropped, not fatal: one bad pointer once discarded
                # Alphabet's whole list. Reading the table whole still finds the total row by its sum.
                self.total_kpi = None
        elif self.kind == "mix":
            if any(kpi.unit != "percent" for kpi in self.kpis):
                raise ValueError(f"{self.key}: mixes hold percentages")
            # A mix's "Total 100%" row is its check, not a share: keep it as the total when it is one of its KPIs.
            if self.total_kpi not in keys:
                self.total_kpi = next((kpi.key for kpi in self.kpis
                                       if re.match(r"total\b", kpi.key) or re.match(r"total\b", kpi.label, re.I)), None)
        else:
            self.total_kpi = None
        return self


class BreakdownName(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    members: dict[str, str] = Field(default_factory=dict)

    @field_validator("members")
    @classmethod
    def _members(cls, value: dict[str, str]) -> dict[str, str]:
        return {key: label.strip() for key, label in value.items() if 0 < len(label.strip()) <= 60}


class Spec(BaseModel):
    groups: list[GroupSpec] = Field(max_length=MAX_GROUPS)
    # Professional names for the company's XBRL breakdowns, keyed by breakdown key; members keyed by member key.
    names: dict[str, BreakdownName] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique(self) -> "Spec":
        keys = [group.key for group in self.groups]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate group keys")
        return self


class Locator(BaseModel):
    kpi: str  # "group_key.kpi_key"
    table: str | None = None
    row: int | None = None
    col: int | None = None
    scale: float | None = None
    block: str | None = None
    quote: str | None = None
    value_text: str | None = None

    @model_validator(mode="after")
    def _one_source(self) -> "Locator":
        in_table = self.table is not None and self.row is not None and self.col is not None
        in_text = self.block is not None and bool(self.quote) and bool(self.value_text)
        if in_table == in_text:
            raise ValueError(f"{self.kpi}: point to exactly one table cell or one quoted sentence")
        return self


class TableLocator(BaseModel):
    """A whole breakdown or mix laid out as one table: code reads every row between first_row and last_row in the
    reported column, so renamed, split or new rows (a new node, a regrouped region) are never missed."""
    group: str
    table: str
    col: int
    first_row: int
    last_row: int
    total_row: int | None = None
    scale: float | None = None

    @model_validator(mode="after")
    def _rows(self) -> "TableLocator":
        if self.last_row < self.first_row:
            raise ValueError(f"{self.group}: last_row before first_row")
        return self


class Located(BaseModel):
    period_end: str | None = None
    # The document reports only the full fiscal year (an annual report); its values are for the year, not a quarter.
    annual: bool = False
    tables: list[TableLocator] = Field(default_factory=list)
    values: list[Locator] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class Review(BaseModel):
    """Pro's verdict on KPIs the two readings disagree on: "A", "B" or "none" per KPI key."""
    choices: dict[str, Literal["A", "B", "none"]] = Field(default_factory=dict)


class Classification(BaseModel):
    kind: Literal["earnings_release", "financial_report", "presentation", "other"]


class Selection(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=400)


class Maintenance(BaseModel):
    """Changes to a company's KPI list: additions only, plus KPIs the company stopped reporting."""
    add_groups: list[GroupSpec] = Field(default_factory=list, max_length=4)
    add_kpis: dict[str, list[KpiSpec]] = Field(default_factory=dict)
    retire: list[str] = Field(default_factory=list)

    @classmethod
    def lenient(cls, data: dict) -> "Maintenance":
        """Keeps every valid addition and drops malformed ones, instead of rejecting the whole audit."""
        groups = []
        for raw in (data.get("add_groups") or [])[:4]:
            try:
                groups.append(GroupSpec.model_validate(raw))
            except ValidationError:
                continue
        kpis: dict[str, list[KpiSpec]] = {}
        for group_key, items in (data.get("add_kpis") or {}).items():
            for raw in items or []:
                try:
                    kpis.setdefault(group_key, []).append(KpiSpec.model_validate(raw))
                except ValidationError:
                    continue
        retire = [str(item) for item in (data.get("retire") or []) if isinstance(item, str)]
        return cls(add_groups=groups, add_kpis=kpis, retire=retire)


class Curation(BaseModel):
    """Rows of an XBRL candidate that together make up revenue; empty when no subset does."""
    members: list[str] = Field(default_factory=list)


class Explanation(BaseModel):
    legitimate: bool
    quote: str | None = None


class Explanations(BaseModel):
    items: dict[str, Explanation] = Field(default_factory=dict)


class AiResponseError(ValueError):
    pass


def parse(text: str, model):
    try:
        data = json.loads(_strip_fences(text))
        if model is Maintenance and isinstance(data, dict):
            return Maintenance.lenient(data)
        return model.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as error:
        raise AiResponseError(str(error)[:500]) from error


def parse_spec(text: str) -> Spec:
    return parse(text, Spec)


def parse_located(text: str) -> Located:
    return parse(text, Located)


def parse_review(text: str) -> Review:
    return parse(text, Review)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    return text


def prompt(company: str, document: str, task: str, data: str = "") -> str:
    """Company, then the document, then the task: everything before the task is shared by every task on a document."""
    return f"Company: {company}\n\n=== DOCUMENT ===\n{document}\n\n=== TASK ===\n{task}" + (f"\n\n{data}" if data else "")


DOCUMENT_FORMAT = """The document is split into tables [T..] with rows r<n> and columns c<n>, and text blocks [P..]. \
Each exhibit's ids carry its own prefix (A_, B_, ...)."""

PROPOSE_TASK = f"""Choose the business KPIs worth tracking every quarter for this company, from its latest earnings \
documents. {DOCUMENT_FORMAT}

Track:
- Revenue breakdowns reported as amounts: by segment, product line, platform, technology, customer type, geography.
- Percentage mixes that split revenue or shipments, e.g. revenue by technology node or by platform.
- Operating metrics that drive the business: users (DAU, MAU, DAP), paid members or subscribers, ARPU/ARPP, units \
shipped or deliveries, wafer shipments, backlog or remaining performance obligations, bookings, gross bookings/GMV, \
trips, same-store sales, store count, net revenue retention, ARR, ad impressions and price per ad growth.

Never track: total revenue on its own, revenue or profit growth rates (including FX-neutral or constant-currency \
growth), costs, margins, operating or net income, EPS, cash flow or balance sheet lines, content, purchase or lease \
obligations, non-GAAP adjustments, guidance or outlook, dividends and buybacks, or anything not given as a number for \
the reported quarter.

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
- kind "revenue_breakdown": currency amounts that together make up revenue. Include the table's total row as a KPI and \
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
- Metric labels are short Title Case nouns: "Employees", "Daily Active People (DAP)", "Paid Memberships", "AWS Revenue \
Run Rate", "Satellites in Orbit". Spell out internal shorthand (WW → Worldwide, Y/Y → Year over Year, 3P → \
Third-Party). Avoid the word "mix" unless it is the company's own term; describe a share plainly ("Third-Party Seller \
Share of Units"). Keep well-known abbreviations the company uses (AWS, DAP, ARPU) and add the full words in brackets \
only when they help.
- When a group you track is the same breakdown as one listed under "Name these XBRL breakdowns" (for example the \
quarterly version of an annual XBRL breakdown), give both the same title and row labels, so they read as one view.
- "names" renames each breakdown listed under "Name these XBRL breakdowns": {{"<breakdown key>": {{"label": "...", \
"members": {{"<member key>": "..."}}}}}}. Use only the keys given; omit a member whose label is already right.
- At most 8 groups and 16 KPIs per group. Prefer fewer, clearly reported KPIs.

JSON shape:
{{"groups": [{{"key": "revenue_by_technology", "label": "Revenue by Technology", "kind": "mix", "total_kpi": null,
  "kpis": [{{"key": "n3", "label": "3nm", "unit": "percent"}}]}}],
 "names": {{"products": {{"label": "Revenue by Market Platform", "members": {{"hyperscale": "Hyperscale"}}}}}}}}"""

LOCATE_TASK = f"""Find where each KPI below has its value for the reported quarter. {DOCUMENT_FORMAT}

For a revenue_breakdown or mix laid out as one table, point to the table once instead of to each row:
  {{"group": "<group>", "table": "B_T68", "col": 1, "first_row": 2, "last_row": 12, "total_row": 13, "scale": 1000}}
  Code reads every row from first_row to last_row in that column, including rows not in the KPI list (a new node, a \
regrouped region), so include every row of the breakdown and nothing else; total_row is the breakdown's total (null if \
none). Use this in "tables".

For each other KPI, in "values", either:
- a table cell: {{"kpi": "<group>.<kpi>", "table": "A_T3", "row": 5, "col": 2, "scale": 1000000}}
  "scale" is the multiplier that applies to that row ("in millions" = 1000000, "in thousands" = 1000, none = 1). A \
header such as "(in millions, except employee data)" does not apply to the rows it excepts: use 1 for them.
- or text: {{"kpi": "<group>.<kpi>", "block": "A_P7", "quote": "<exact excerpt, max 160 chars>", "value_text": "3.60 billion"}}
  The quote is copied character for character from that block and contains value_text, the number exactly as written \
with its unit word or % sign. Ids containing P are text blocks: quote them; only ids containing T are tables.

Rules:
- Use the reported quarter's column (three months ended at the period end), never the prior-year quarter, the prior \
quarter, year-to-date or full-year columns.
- For a percentage, point to the percentage itself, not an amount.
- If a KPI is not reported for this quarter, list its key in "missing". Do not guess.
- "period_end" is the last day of the reported quarter as YYYY-MM-DD.
- If the document reports only full-year figures for the period (an annual report with no three-month column), use \
the full-year column, set "annual": true, and use the fiscal year end as "period_end".

JSON shape: {{"period_end": "2026-06-30", "annual": false, "tables": [...], "values": [...], "missing": ["operating.dap"]}}"""

REVIEW_TASK = """Two independent readings of this document disagree on the KPIs below. For each, decide which reading \
is the value for the reported quarter, checking the document yourself. Answer "A" or "B", or "none" if neither is the \
reported quarter's value for that KPI (a prior-year column, a year-to-date figure, a different metric, or a KPI the \
document does not report).

JSON shape: {"choices": {"<group>.<kpi>": "A"}}"""

CLASSIFY_TASK = """Classify this furnished filing by what it is:
- "earnings_release": the company's quarterly or annual results announcement (press release, shareholder letter, \
management report with the quarter's results).
- "financial_report": quarterly or annual financial statements with notes (e.g. a foreign issuer's interim report).
- "presentation": the results presentation or slides for a quarter.
- "other": anything else (governance, dividends, debt offerings, monthly sales, investor days, M&A).

JSON shape: {"kind": "earnings_release"}"""

SELECT_TASK = """This document is too long to read whole. From its outline, choose the ids of every table and text \
block that could hold the KPIs listed below or a breakdown of revenue (by segment, product, platform, technology, \
geography), and the passage naming the reporting period. Prefer too many over too few; leave out legal notes, \
leases, debt, tax, pensions, equity and related-party tables.

JSON shape: {"ids": ["A_T12", "A_P3"]}"""

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

CURATE_TASK = """An XBRL revenue breakdown does not add up to reported revenue as tagged: it may include a parent row \
together with its children, a detail row that overlaps another (e.g. "U.S." beside "U.S. and Canada"), or rows from \
a different split. Choose the member keys that together make up total revenue exactly once, with no overlaps. Return \
an empty list if no subset is a genuine breakdown of total revenue.

JSON shape: {"members": ["us_and_canada", "europe", "asia_pacific", "rest_of_world"]}"""

EXPLAIN_TASK = """The values below changed sharply from the previous quarter. For each, decide from the document \
whether the change is real (for example a reorganisation, acquisition, divestiture, restatement, stock split, a new \
product ramp, or a change of definition the company states). If real, quote the exact sentence that shows it \
(max 200 characters, copied character for character). If the document gives no reason, answer false.

JSON shape: {"items": {"<group>.<kpi>": {"legitimate": true, "quote": "..."}}}"""


def propose_data(covered: list[str], to_name: list[str], current: "Spec | None" = None) -> str:
    covered_text = "\n".join(f"- {label}" for label in covered) or "- none"
    naming_text = "\n".join(to_name) or "- none"
    current_text = ("" if current is None or not current.groups else
                    "\n\nCurrent list (keep every group and key the documents still report; improve labels and add "
                    f"what is missing, but never rename a key):\n{kpi_lines(current)}")
    return (f"Already covered by XBRL (do not repeat):\n{covered_text}\n\n"
            f"Name these XBRL breakdowns (key: current title; member key = current label):\n{naming_text}{current_text}")


def kpi_lines(spec: Spec, hints: dict[str, str] | None = None) -> str:
    lines = []
    for group in spec.groups:
        for kpi in group.kpis:
            key = f"{group.key}.{kpi.key}"
            hint = f" (last quarter: {hints[key]})" if hints and key in hints else ""
            lines.append(f"- {key}: {group.label} / {kpi.label} [{kpi.unit}]{hint}")
    return "\n".join(lines)


def locate_data(period_hint: str, spec: Spec, hints: dict[str, str]) -> str:
    return f"Reported quarter: {period_hint}\nKPIs:\n{kpi_lines(spec, hints)}"


def normalize_spec(spec: Spec) -> Spec:
    """A list whose every breakdown can be checked: a revenue breakdown needs at least two parts, a mix two shares.
    Anything less can never reconcile (Broadcom's lone "AI Semiconductor" amount was a one-row breakdown, hidden every
    quarter), so its KPIs become metrics, which are checked one by one."""
    groups, loose = [], []
    for group in spec.groups:
        parts = [kpi for kpi in group.kpis if kpi.key != group.total_kpi]
        # A breakdown without a named total still reconciles: reading the table whole finds its total row by the sum.
        if group.kind == "revenue_breakdown" and len(parts) < 2:
            loose += parts or group.kpis
        elif group.kind == "mix" and len(parts) < 2:
            loose += group.kpis
        else:
            groups.append(group.model_dump())
    if loose:
        metrics = next((group for group in groups if group["kind"] == "metric"), None)
        if metrics is None:
            metrics = {"key": "operating", "label": "Operating Metrics", "kind": "metric", "total_kpi": None, "kpis": []}
            groups.append(metrics)
        taken = {kpi["key"] for kpi in metrics["kpis"]}
        for kpi in loose:
            if kpi.key not in taken and len(metrics["kpis"]) < MAX_KPIS_PER_GROUP:
                metrics["kpis"].append(kpi.model_dump())
                taken.add(kpi.key)
    elif sum(len(group.kpis) for group in spec.groups) <= MAX_LISTED_KPIS:
        return spec
    groups = [g for g in groups if g["kpis"]][:MAX_GROUPS]
    # Over the cap, only operating metrics are trimmed (from the end of the list, keeping a few): a breakdown or mix is
    # checked by its own sum, so it is never dropped (TSMC's geography beside its twelve nodes).
    def count() -> int:
        return sum(len(g["kpis"]) for g in groups)
    for group in [g for g in groups if g["kind"] == "metric"]:
        while count() > MAX_LISTED_KPIS and len(group["kpis"]) > MIN_KEPT_METRICS:
            group["kpis"].pop()
    return Spec.model_validate({"groups": groups, "names": spec.names})
