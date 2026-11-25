"""Where each line of an owner-built list comes from: business_kpi_specs.sources, one mapping per line.

A line has up to two code sources, and the refresh follows nothing else:
- `release`: a cell of its earnings release's table (8-K exhibit): the table's anchor text, the row's label, the column
  the quarter is in, and the table's scale.
- `report`: its 10-Q / 10-K, either an XBRL fact (concepts, the exact member set, wrapper members to ignore) or a cell
  of a table in the report (as `release`).
A line with neither (`hand` says why: Dell's AI orders are a sentence in the CEO's comment, not a table cell) goes to
the hand-reading queue each new quarter. So does a line with no mapping at all.

Example (Dell's ISG AI-optimized servers):
    "isg_lines.isg_ai_servers": {
      "release": {"kind": "table", "anchor": "Segment Information", "row": "AI-Optimized Servers", "scale": 1e6},
      "report": {"kind": "xbrl", "concepts": ["Revenues"],
                 "members": {"StatementBusinessSegmentsAxis": "InfrastructureSolutionsGroupMember",
                             "ProductOrServiceAxis": "AIOptimizedServersAndNetworkingMember"},
                 "ignore": ["OperatingSegmentsMember"]}}
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# What a figure covers: the period's own three months, the fiscal year to date (a Q3 report's nine months, which
# Q4 = FY - nine months needs), the whole fiscal year, or a balance at the period's end (backlog, subscribers).
Duration = Literal["quarter", "ytd", "fy", "instant"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class XbrlSource(_Strict):
    kind: Literal["xbrl"] = "xbrl"
    # Local names ("Revenues"); the line's value is the one figure they give (two concepts giving two figures: ambiguous).
    concepts: list[str] = Field(min_length=1)
    # The fact's dimensions, exactly (axis local name -> member local name), once wrapper members are dropped; {} is the
    # concept with no dimension (the consolidated total).
    members: dict[str, str] = Field(default_factory=dict)
    # Wrapper members the filer may or may not add around the same figure ("OperatingSegmentsMember" on Dell's segment
    # facts, the ConsolidationItemsAxis's segment members): dropped before the member set is compared.
    ignore: list[str] = Field(default_factory=list)
    sign: Literal[1, -1] = 1
    # A balance (backlog) rather than a flow: an instant fact at the period's end.
    instant: bool = False


class TableSource(_Strict):
    kind: Literal["table"] = "table"
    # Text (a regular expression, case-insensitive) in the table's caption or the text before it: "Segment Information".
    anchor: str
    # The row that opens the rows searched (its label), when the label alone repeats in the table: Dell's "Corporate and
    # other" is in both the revenue and the operating income reconciliation.
    section: str | None = None
    # The row's label, matched whole (case-insensitive; footnote markers like "(a)" dropped): "Total ISG net revenue".
    row: str
    # The column: by its header's date and duration ("Three Months Ended / October 30, 2026") when None, or this index.
    column: int | None = None
    # What the table's figures are in: 1e6 for "(in millions)". A table declaring another scale is a basis change.
    scale: float = 1.0
    sign: Literal[1, -1] = 1
    instant: bool = False

    @field_validator("scale")
    @classmethod
    def _positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("scale must be positive")
        return value


LineSource = Annotated[Union[XbrlSource, TableSource], Field(discriminator="kind")]


class LineRef(_Strict):
    """A group's total that is another line of the list in the same period: Dell's ISG lines add up to segments.isg."""
    kind: Literal["line"] = "line"
    line: str


TotalSource = Annotated[Union[XbrlSource, TableSource, LineRef], Field(discriminator="kind")]


class LineMapping(_Strict):
    release: TableSource | None = None
    report: LineSource | None = None
    # Why the line has no code source: it is read by hand every quarter (the hand-reading queue).
    hand: str | None = None
    # A point-in-time figure (backlog, subscribers): no FY row, and its Q4 is read at the year's end, not derived.
    balance: bool = False


class GroupMapping(_Strict):
    # The total the group's lines add up to, in the release and in the report. Without one the group's figures stand on
    # their single cell or fact alone, except a breakdown's, which are held: a breakdown that cannot be tied is not served
    # unchecked.
    release_total: TotalSource | None = None
    report_total: TotalSource | None = None
    # Members of the lines' axes the filing may tag beside them that are not lines (eliminations, a subtotal): never
    # taken for a new segment.
    known_members: list[str] = Field(default_factory=list)
    # Whether 10-Ks give the group FY rows (breakdowns and flow metrics the 10-K prints for the year).
    fy_rows: bool = True


class OwnerSources(_Strict):
    version: Literal[1] = 1
    lines: dict[str, LineMapping] = Field(default_factory=dict)
    groups: dict[str, GroupMapping] = Field(default_factory=dict)
    # Where the mapping came from (the build script, a derivation run), for the owner's record.
    derived_from: str | None = None

    @field_validator("lines")
    @classmethod
    def _keys(cls, lines: dict) -> dict:
        for key in lines:
            if key.count(".") != 1 or not all(key.split(".")):
                raise ValueError(f"line key {key!r} is not group.kpi")
        return lines

    def line(self, group: str, kpi: str) -> LineMapping | None:
        return self.lines.get(f"{group}.{kpi}")

    def group(self, group: str) -> GroupMapping:
        return self.groups.get(group) or GroupMapping()


def parse_sources(raw) -> tuple[OwnerSources, str | None]:
    """The stored mapping, or an empty one and why: a mapping that does not parse sends every line to the hand queue
    rather than the refresh guessing at it."""
    if not raw:
        return OwnerSources(), None
    try:
        return OwnerSources.model_validate(raw), None
    except ValidationError as error:
        return OwnerSources(), f"the stored line sources do not parse: {str(error).splitlines()[0][:200]}"
