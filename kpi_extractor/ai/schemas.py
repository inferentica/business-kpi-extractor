"""The schemas an AI answer must pass, and the KPI list they describe: groups, KPIs, locators and each task's answer."""

from __future__ import annotations

from datetime import date, timedelta
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from typing import Literal
import re



GroupKind = Literal["revenue_breakdown", "mix", "metric"]


Unit = Literal["currency", "percent", "count", "ratio"]


_KEY = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


MAX_GROUPS = 8


# A breakdown keeps its history: TSMC lists today's merged node buckets beside the finer nodes they replaced (17).
MAX_KPIS_PER_GROUP = 24


# A focused list: every KPI is read, and possibly disputed, every quarter. Propose is told these limits; the code
# keeps any list to this many KPIs in all, trimming operating metrics from the end of their group (see normalize_spec).
MAX_LISTED_KPIS = 24


MIN_KEPT_METRICS = 10


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
    # The KPI of its own group it is a line of (UnitedHealth's Optum Health under Total Optum; Alphabet's Search under
    # Google advertising, under Google Services), set by the owner: lines add up to it, and it is a part of the group.
    parent: str | None = None
    # The periods the KPI is reported for, set by the owner (period ends, YYYY-MM-DD): JPMorgan's advisors under today's
    # definition only since 2023, TSMC's Asia only until fiscal 2017. Outside them nothing is read or kept.
    since: str | None = None
    until: str | None = None

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        return _check_key(value)

    @field_validator("since", "until")
    @classmethod
    def _day(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        date.fromisoformat(str(value)[:10])
        return str(value)[:10]

    def reported_at(self, period_end) -> bool:
        """Whether the KPI is reported for a period ending on this day (a few days' grace for week-based calendars)."""
        day = period_end if isinstance(period_end, date) else date.fromisoformat(str(period_end)[:10])
        return not (self.since and day < date.fromisoformat(self.since) - timedelta(days=10)
                    or self.until and day > date.fromisoformat(self.until) + timedelta(days=10))


def for_period(spec: "Spec", period_end) -> "Spec":
    """The list as it stands for one period: KPIs outside their reported periods left out, and a group with none left."""
    if all(kpi.reported_at(period_end) for group in spec.groups for kpi in group.kpis):
        return spec
    groups = []
    for group in spec.groups:
        kpis = [kpi for kpi in group.kpis if kpi.reported_at(period_end)]
        if kpis:
            total = group.total_kpi if any(kpi.key == group.total_kpi for kpi in kpis) else None
            groups.append(group.model_copy(update={"kpis": kpis, "total_kpi": total}))
    return spec.model_copy(update={"groups": groups})


class GroupSpec(BaseModel):
    key: str
    label: str = Field(min_length=1, max_length=80)
    kind: GroupKind
    total_kpi: str | None = None
    # The name the owner gave the view on /kpi-templates, shown wherever the view is (the label stays what the readers
    # are told, so renaming a view never changes a prompt).
    name: str | None = Field(default=None, max_length=80)
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
        # A parent must be another KPI of the group, never the total, and never lead back round to the line: else dropped.
        parent_of = {kpi.key: kpi.parent for kpi in self.kpis}
        for kpi in self.kpis:
            seen, at = {kpi.key}, kpi.parent
            while at is not None and at in parent_of and at not in seen and at != self.total_kpi:
                seen.add(at)
                at = parent_of[at]
            if kpi.parent is not None and (kpi.parent not in parent_of or at is not None):
                kpi.parent = None
                parent_of[kpi.key] = None  # a loop broken once: the rest of it stands
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
    # The row's own label as the reader saw it (read back by code against the cell's row).
    row_label: str | None = None
    # The document's own words stating the scale ("in millions", "(Bil.)"), checked by code when it finds none itself.
    scale_text: str | None = None
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
    # The labels of first_row and last_row as the reader saw them (read back by code).
    first_label: str | None = None
    last_label: str | None = None
    scale_text: str | None = None

    @model_validator(mode="after")
    def _rows(self) -> "TableLocator":
        if self.last_row < self.first_row:
            raise ValueError(f"{self.group}: last_row before first_row")
        return self


# Why a reader found no value for a KPI (Locate) or why a reading is not the quarter's value (Review). Anything else a
# reader answers is kept as "unstated", so a new wording never fails a reading.
# The AI is not told whether it sees a whole document, so whether a figure was left out of a long one is decided by
# code (left_out_names_it, in the filing's notes), never asked of the reader.
MISSING_REASONS = ("not_reported", "elsewhere", "related_only", "guidance_only")


REVIEW_REASONS = ("prior_period", "year_to_date", "full_year", "guidance", "different_metric", "wrong_row", "wrong_scale",
                  "not_reported")


def _codes(raw: dict, allowed: tuple[str, ...]) -> dict[str, str]:
    return {str(key): (str(code) if str(code) in allowed else "unstated") for key, code in (raw or {}).items()}


class Located(BaseModel):
    period_end: str | None = None
    # The document reports only the full fiscal year (an annual report); its values are for the year, not a quarter.
    annual: bool = False
    tables: list[TableLocator] = Field(default_factory=list)
    values: list[Locator] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    missing_reasons: dict[str, str] = Field(default_factory=dict)

    @field_validator("missing_reasons", mode="before")
    @classmethod
    def _missing_codes(cls, raw):
        return _codes(raw if isinstance(raw, dict) else {}, MISSING_REASONS)


class Review(BaseModel):
    """The reasoning reader's verdict on KPIs the two readings disagree on: "A", "B" or "none" per KPI key, with the
    reason the reading it turned down (or both, for "none") is not the quarter's value."""
    choices: dict[str, Literal["A", "B", "none"]] = Field(default_factory=dict)
    reasons: dict[str, str] = Field(default_factory=dict)

    @field_validator("reasons", mode="before")
    @classmethod
    def _review_codes(cls, raw):
        return _codes(raw if isinstance(raw, dict) else {}, REVIEW_REASONS)


class Classification(BaseModel):
    kind: Literal["earnings_release", "financial_report", "presentation", "other"]


class Selection(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=400)


class TagPicks(BaseModel):
    """For each KPI key, the id of the tagged figure that is that KPI, or null."""
    picks: dict[str, str | None] = Field(default_factory=dict)


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
