"""Figures only a slide's chart gives: pies and unit bars, read by OCR with no AI."""
from __future__ import annotations

import os
import re
import urllib.request
from dataclasses import replace
from datetime import date, timedelta

from ..charts import (ChartBreakdown, ChartUnits, breakdown_slides, ocr_words, read_breakdowns, read_growth_rates,
                      read_titled_pie, read_units, titled_pie_slides, unit_region)
from ..fiscal import fiscal_label
from ..release_statements import release_statements, reported_units
from ..sec import FilingRef
from ..xbrl import member_key
from .shared import AI_GROUP_PREFIX, CHART_METHOD

# A breakdown slide gives this many things a quarter (three pies and the units sold); a quarter with fewer is looked
# at again.
CHART_GROUPS = 4
# The bar chart of units is read again at these enlargements; a count is trusted when readings agree.
UNIT_SCALES = (3, 4)
# The enlargement a slide is read again at when a pie is missing from the first reading.
PIE_SCALE = 3
_SYSTEMS_SOLD = re.compile(r"lithography systems sold", re.I)

def missing_pies(pies: list) -> bool:
    """Whether a slide's reading lacks a pie: every quarter it shows has a technology, end-use and region pie."""
    if not pies:
        return False
    quarters = {(pie.fiscal_year, pie.fiscal_period) for pie in pies}
    axes = {pie.axis for pie in pies} | {"technology", "end-use", "region"}
    return len(pies) < len(quarters) * len(axes)


def fill_pies(pies: list, closer: list) -> list:
    """The pies a closer reading has that the first did not."""
    have = {(pie.axis, pie.fiscal_year, pie.fiscal_period) for pie in pies}
    return [pie for pie in closer if (pie.axis, pie.fiscal_year, pie.fiscal_period) not in have]


def _fetch_image(link: str) -> bytes:
    request = urllib.request.Request(link, headers={"User-Agent": os.environ.get("SEC_USER_AGENT", "TradeTracker admin@inferentica.com")})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()



class ChartReading:
    """Figures only a slide's chart gives: pies and unit bars, read by OCR with no AI."""

    def _read_titled_pies(self, releases: list[FilingRef]) -> list[dict]:
        """A quarter's platform split from its results-day slide (TSMC's "3Q26 Revenue by Platform"), read by the local
        OCR model weeks before the quarter's report gives the amounts. The slide's growth chart grows each platform's
        reported amount of the quarter before ("+22%"), scaled to the quarter's net revenue, which the release states;
        the pie's rounded shares must agree with the result. Whole-point shares alone would misstate the small
        platforms (a 4% slice is anything from 3.5% to 4.5%). The figures go into the series the company's reports fill
        (matched by the members' names), and the report's own figures replace them. Only a quarter it lacks is read.

        The technology pie is a split of wafer revenue, an amount the release does not give: its shares are stored as
        shares (Bedrock's percentage chart shows them) until the report's amounts replace them."""
        stored: list[dict] = []
        for ref in sorted((ref for ref in releases if ref.period_end), key=lambda item: item.filed, reverse=True)[:2]:
            try:
                quarter = fiscal_label(ref.period_end, self.profile.fiscal_year_end, year_offset=self.offset)
                slides = []
                for exhibit in ref.exhibits[:6]:
                    raw = self._waiting(self._exhibit(exhibit).result)
                    slides.extend((urllib.parse.urljoin(exhibit.url, source), axis, year, period)
                                  for source, axis, year, period in titled_pie_slides(raw or ""))
                records: list[dict] = []
                for link, axis, year, period in slides:
                    if (year, period) != quarter:
                        continue
                    group = self._series_for(axis)
                    # Read once: the quarter's report, or this reading already done (figures stored with their rates).
                    if group is None or any(value["group_key"] == group and (value["fiscal_year"], value["fiscal_period"]) == quarter
                                            and value["validation_status"] == "verified"
                                            and (value["method"] != CHART_METHOD or {"growth", "share_of"} & set(value.get("locator") or {}))
                                            for value in self.values.values()):
                        continue
                    words = ocr_words(_fetch_image(link))
                    pie = read_titled_pie(words, axis, year, period)
                    if pie is None:
                        continue
                    if axis == "technology":
                        records.extend(self._technology_share_records(pie, group, ref, link))
                        continue
                    growth, base = read_growth_rates(words), self._release_revenue(ref)
                    if growth is None or base is None:
                        continue
                    records.extend(self._titled_pie_records(pie, growth, group, base, ref, link))
                if records:
                    stored += records
                    self.log(f"{self.symbol}: {ref.form} {ref.filed} pie figures read ({len(records)} values)")
            except Exception as error:  # noqa: BLE001
                self.log(f"{self.symbol}: pies of {ref.form} {ref.filed} not read ({type(error).__name__}: {error})"[:300])
        return stored

    def _technology_share_records(self, pie: ChartBreakdown, group: str, ref: FilingRef, link: str) -> list[dict]:
        """A technology pie's shares of wafer revenue, under the series' members, as percentages. The release's own
        words ("2-nanometer accounted for 8% of total wafer revenue") must agree with the shares they name."""
        members: dict[str, dict] = {}
        for value in sorted(self.values.values(), key=lambda item: (item["fiscal_year"], item["fiscal_period"])):
            if value["group_key"] == group and value["validation_status"] == "verified" and value["unit"] == "currency":
                members[member_key(value["kpi_label"])] = value
        if any(member_key(name) not in members for name in pie.shares):
            return []
        try:
            text = re.sub(r"\s+", " ", " ".join(block.text for block in self._document(ref).blocks.values()))
        except Exception:  # noqa: BLE001 - without the release's words there is nothing to check against
            return []
        stated = {f"{node}nm": float(share) for node, share in
                  re.findall(r"(\d{1,2})-nanometer accounted for (\d{1,3})%", text)}
        if not stated or any(pie.shares.get(name) != share for name, share in stated.items()):
            return []
        return [self._record(
            group_key=group, group_label=members[member_key(name)]["group_label"],
            group_kind=members[member_key(name)]["group_kind"], group_order=members[member_key(name)]["group_order"],
            kpi_key=members[member_key(name)]["kpi_key"], kpi_label=members[member_key(name)]["kpi_label"],
            kpi_order=members[member_key(name)]["kpi_order"], unit="percent", value=share, currency=None,
            method=CHART_METHOD, validation_status="verified", reconciliation_error_pct=round(sum(pie.shares.values()) - 100, 2),
            notes=[f"the results slide's share of wafer revenue; its amount comes with the quarter's report"]
                  + (["16/20nm together on the slide"] if name == "16nm" else []),
            ref=ref, source_url=link, fiscal_year=pie.fiscal_year, fiscal_period=pie.fiscal_period, period_end=ref.period_end,
            locator={"image": link, "share": share, "share_of": "wafer revenue"},
        ) for name, share in pie.shares.items()]

    def _titled_pie_records(self, pie: ChartBreakdown, growth: dict[str, float], group: str,
                            base: tuple[float, str | None], ref: FilingRef, link: str) -> list[dict]:
        """Each platform: the quarter before's reported amount grown by the slide's rate, all scaled together to the
        quarter's net revenue. Kept only when that scaling is slight (the rates are rounded to points) and every
        platform's resulting share is the pie's to within its rounding."""
        quarter = (pie.fiscal_year, pie.fiscal_period)
        year, number = int(pie.fiscal_year), int(pie.fiscal_period[1])
        before = (str(year - 1), "Q4") if number == 1 else (str(year), f"Q{number - 1}")
        members: dict[str, dict] = {}
        previous: dict[str, float] = {}
        for value in sorted(self.values.values(), key=lambda item: (item["fiscal_year"], item["fiscal_period"])):
            if value["group_key"] != group or value["validation_status"] != "verified":
                continue
            members[member_key(value["kpi_label"])] = value
            if (value["fiscal_year"], value["fiscal_period"]) == before:
                previous[member_key(value["kpi_label"])] = float(value["value"])
        names = {member_key(name): name for name in pie.shares}
        if set(names) - set(members) or set(names) - set(previous) or {member_key(name) for name in growth} != set(names):
            return []
        amount, currency = base
        grown = {key: previous[key] * (1 + growth[names[key]] / 100) for key in names}
        factor = amount / sum(grown.values())
        if abs(factor - 1) > 0.015:
            return []
        values = {key: figure * factor for key, figure in grown.items()}
        if any(abs(values[key] / amount * 100 - pie.shares[names[key]]) > 0.6 for key in names):
            return []
        return [self._record(
            group_key=group, group_label=members[key]["group_label"], group_kind=members[key]["group_kind"],
            group_order=members[key]["group_order"], kpi_key=members[key]["kpi_key"], kpi_label=members[key]["kpi_label"],
            kpi_order=members[key]["kpi_order"], unit="currency", value=round(values[key], 2),
            currency=currency or members[key].get("currency"), method=CHART_METHOD, validation_status="verified",
            reconciliation_error_pct=round((factor - 1) * 100, 2),
            notes=[f"{before[1]} {before[0]} grown {growth[names[key]]:+g}% as the results slide shows, scaled to net revenue;"
                   f" the pie's share is {pie.shares[names[key]]:g}%"],
            ref=ref, source_url=link, fiscal_year=quarter[0], fiscal_period=quarter[1], period_end=ref.period_end,
            locator={"image": link, "share": pie.shares[names[key]], "growth": growth[names[key]], "total": amount},
        ) for key in names]

    def _read_chart_breakdowns(self, releases: list[FilingRef]):
        """What a release shows only as charts (ASML's slide: net system sales by technology, end-use and region as
        pies, and units sold by technology as bars), read from the slide's picture by a local OCR model, no AI service.

        A pie is a split of the amount it names, so it is stored as a revenue breakdown: each share of that quarter's
        net system sales, which the release's statements give. Units are stored as operating metrics and must add up
        to the quarter's reported units. A slide shows its quarter and the one before, so what one slide could not
        give is taken from the next. A slide that cannot be read never holds up the KPIs. Hands back each release's rows
        as it finishes it, so a long first reading of the slides keeps what it has read."""
        period_ends = {fiscal_label(ref.period_end, self.profile.fiscal_year_end, year_offset=self.offset): ref.period_end
                       for ref in releases if ref.period_end}
        chart_values = [value for value in self.values.values()
                        if value["method"] == CHART_METHOD and value["validation_status"] == "verified"]
        # Shares once stored as bare percentages are read again as amounts.
        have = {(value["group_key"], value["fiscal_year"], value["fiscal_period"]) for value in chart_values
                if value["unit"] != "percent"}
        groups = {key[0] for key in have}
        ordered = sorted(period_ends, key=period_ends.get)

        def complete(quarter: tuple[str, str]) -> bool:
            return len(groups) >= CHART_GROUPS and all((group, *quarter) in have for group in groups)

        # Pies a point off 100, waiting for a second slide to show the same shares.
        unconfirmed: dict[tuple[str, str, str], dict[str, float]] = {}
        for ref in sorted(releases, key=lambda item: item.filed):
            if ref.period_end is None:
                continue
            try:
                own = fiscal_label(ref.period_end, self.profile.fiscal_year_end, year_offset=self.offset)
                before = ordered[ordered.index(own) - 1] if own in ordered and ordered.index(own) else None
                if complete(own) and (before is None or complete(before)):
                    continue  # this slide's two quarters are already stored whole
                records: list[dict] = []
                for exhibit in ref.exhibits[:6]:
                    raw = self._waiting(self._exhibit(exhibit).result)
                    for source, _title in breakdown_slides(raw or ""):
                        link = urllib.parse.urljoin(exhibit.url, source)
                        image = _fetch_image(link)
                        words = ocr_words(image)
                        # The bars' small figures are read again from an enlarged cut of the slide.
                        region = unit_region(words)
                        details = [ocr_words(image, region, scale) for scale in UNIT_SCALES] if region else []
                        pies = read_breakdowns(words)
                        if missing_pies(pies):
                            # A share misread at the slide's own size ("39%" as "%68") leaves its pie out; the slide
                            # read again enlarged gives the pies the first reading missed, under the same checks.
                            pies = [*pies, *fill_pies(pies, read_breakdowns(ocr_words(image, (0, 0), PIE_SCALE)))]
                        for chart in [*pies, *read_units(words, *details)]:
                            quarter = (chart.fiscal_year, chart.fiscal_period)
                            if quarter not in period_ends:
                                continue
                            rows = (self._unit_records(chart, ref, period_ends[quarter], link) if isinstance(chart, ChartUnits)
                                    else self._chart_records(chart, ref, period_ends[quarter], link))
                            if not rows or (rows[0]["group_key"], *quarter) in have:
                                continue
                            if isinstance(chart, ChartBreakdown) and not chart.exact:
                                key = (rows[0]["group_key"], *quarter)
                                if unconfirmed.get(key) != chart.shares:
                                    unconfirmed[key] = dict(chart.shares)
                                    continue
                            records.extend(rows)
                            have.add((rows[0]["group_key"], *quarter))
                            groups.add(rows[0]["group_key"])
                if records:
                    self.log(f"{self.symbol}: {ref.form} {ref.filed} chart figures read ({len(records)} values)")
                    yield records
            except Exception as error:  # noqa: BLE001
                self.log(f"{self.symbol}: charts of {ref.form} {ref.filed} not read ({type(error).__name__}: {error})"[:300])

    def _bare_shares(self) -> list[dict]:
        # A share left as a bare percentage (its quarter's amount is not known) is not a revenue breakdown: withdrawn.
        return [{**value, "validation_status": "rejected", "notes": ["a share without its amount; read again as a revenue breakdown"]}
                 for value in self.values.values()
                 if value["method"] == CHART_METHOD and value["unit"] == "percent" and value["validation_status"] != "rejected"
                 # A results-day share of an amount no release gives (TSMC's wafer revenue) is kept as a share.
                 and "share_of" not in (value.get("locator") or {})]

    def _quarter_amount(self, subject: str, quarter: tuple[str, str], ref: FilingRef, period_end: date) -> tuple[float, str | None] | None:
        """The amount a pie splits ("Net system sales") for a quarter: the verified value read for it, else the line of
        that name in the release's own statements."""
        name = member_key(subject)
        for value in self.values.values():
            if ((value["fiscal_year"], value["fiscal_period"]) == quarter and value["unit"] == "currency"
                    and value["validation_status"] == "verified" and value["method"] != CHART_METHOD
                    and member_key(value["kpi_label"]) == name):
                return float(value["value"]), value.get("currency")
        for statement in release_statements(self._document(ref)):
            if statement.statement == "income" and abs((statement.period_end - period_end).days) <= 3:
                for line in statement.lines:
                    if not line["is_heading"] and member_key(line["label"]) == name and line["value"]:
                        return float(line["value"]), statement.currency
        return None

    def _chart_records(self, chart, ref: FilingRef, period_end: date, link: str) -> list[dict]:
        base = self._quarter_amount(chart.subject, (chart.fiscal_year, chart.fiscal_period), ref, period_end)
        if base is None:
            return []
        amount, currency = base
        axis_order = {"technology": 0, "end-use": 1, "region": 2}
        subject_key = re.sub(r"[^a-z0-9]+", "_", chart.subject.lower()).strip("_")
        axis_key = re.sub(r"[^a-z0-9]+", "_", chart.axis.lower()).strip("_")
        total = sum(chart.shares.values())
        ranked = sorted(chart.shares.items(), key=lambda item: -item[1])
        # The slide prints whole percentages, which need not add up to exactly 100: each share is taken of their sum,
        # so the parts add up to the quarter's amount.
        return [self._record(
            group_key=f"{AI_GROUP_PREFIX}{subject_key}_by_{axis_key}", group_label=f"{chart.subject} by {chart.axis}",
            group_kind="revenue_breakdown", group_order=60 + axis_order.get(chart.axis, 9),
            kpi_key=member_key(name)[:48] or "member", kpi_label=name, kpi_order=order, unit="currency",
            value=round(amount * share / total, 2), currency=currency or "USD", method=CHART_METHOD,
            validation_status="verified", reconciliation_error_pct=round(total - 100, 2),
            notes=[f"the slide's rounded share ({share:g}%) of {chart.subject.lower()}"], ref=ref, source_url=link,
            fiscal_year=chart.fiscal_year, fiscal_period=chart.fiscal_period, period_end=period_end,
            locator={"image": link, "share": share, "total": amount},
        ) for order, (name, share) in enumerate(ranked)]

    def _unit_records(self, chart, ref: FilingRef, period_end: date, link: str) -> list[dict]:
        """Units sold by technology. They must add up to the quarter's reported units (new and used systems) where
        those are known; a single count the picture did not give up is that total less the others."""
        quarter = (chart.fiscal_year, chart.fiscal_period)
        reported = [float(value["value"]) for value in self.values.values()
                    if (value["fiscal_year"], value["fiscal_period"]) == quarter and value["unit"] == "count"
                    and value["validation_status"] == "verified" and value["method"] != CHART_METHOD
                    and _SYSTEMS_SOLD.search(value["kpi_label"])]
        total = sum(reported) if len(reported) == 2 else None  # new and used
        # The release's own table of units a quarter is the better witness: it is read by rule, not by the AI.
        try:
            stated = next((units for end, units in reported_units(self._document(ref)).items()
                           if abs((end - period_end).days) <= 3), None)
        except Exception:  # noqa: BLE001 - a document that cannot be read leaves the other witness
            stated = None
        if stated is not None:
            total = float(stated)
        units = dict(chart.units)
        notes = ["read from the slide's chart"]
        if total is None and set(units) - chart.confirmed:
            return []  # nothing to check the bars against: every count must be one two readings agree on
        if chart.missing:
            rest = None if total is None else total - sum(units.values())
            # A fill takes whatever is left of the total, so it would hide a misread digit among the bars that were
            # read: each of those must be a count two readings of the picture agree on.
            if rest is None or rest < 0 or set(units) - chart.confirmed:
                return []
            units[chart.missing] = int(rest)
            notes = [f"{chart.missing}: the quarter's {total:g} units less the other technologies"]
        elif total is not None and sum(units.values()) != total:
            return []
        order = {"EUV": 0, "ArFi": 1, "ArF Dry": 2, "KrF": 3, "I-line": 4}
        return [self._record(
            group_key=f"{AI_GROUP_PREFIX}lithography_units_sold", group_label="Lithography units sold", group_kind="metric",
            group_order=70, kpi_key=f"{member_key(name)[:40]}units", kpi_label=f"{name} units sold",
            kpi_order=order.get(name, 9), unit="count", value=float(count), currency=None, method=CHART_METHOD,
            validation_status="verified", reconciliation_error_pct=None,
            notes=notes if name == chart.missing or not chart.missing else ["read from the slide's chart"],
            ref=ref, source_url=link, fiscal_year=chart.fiscal_year, fiscal_period=chart.fiscal_period,
            period_end=period_end, locator={"image": link, **({"total": total} if total is not None else {})},
        ) for name, count in units.items()]

    def _continuing_split(self, group, period_end: date):
        """Of two complete splits on one axis, the one the company's stored quarters of the past year use, so the year
        and its quarters line up (Broadcom's regions, not its 10-K's countries). A year with no such quarters follows
        the latest ones (Adobe's FY2016-18, before its 10-Qs gave regions); the finer split when nothing is stored."""
        if not group.alternative:
            return group
        window = (period_end - timedelta(days=370)).isoformat()
        # The quarters only: the year before may be on the 10-K's other split (Adobe's FY2018 countries).
        quarters = [v for v in self.values.values() if v["method"] == "xbrl" and v["group_key"] == group.key
                    and v["validation_status"] == "verified" and v["fiscal_period"] != "FY"]
        rows = [v for v in quarters if window <= v["period_end"] < period_end.isoformat()]
        if not rows and quarters:
            latest = max(v["period_end"] for v in quarters)
            rows = [v for v in quarters if v["period_end"] == latest]
        recent = {v["kpi_key"] for v in rows}
        # The same element under another label ("Total Americas" in Adobe's 10-K, "Americas" in its 10-Qs) is one line,
        # whichever quarter names it.
        # The 2018 taxonomy moved members such as AmericasMember from us-gaap to srt: one element either way.
        def standard(member: str | None) -> str | None:
            return re.sub(r"^(?:us-gaap|srt):", "std:", member) if member else None

        stored = {standard(member): v for v in sorted(quarters, key=lambda v: v["period_end"])
                  if (member := (v.get("locator") or {}).get("member"))}
        renamed = {key: stored[element] for key, _label, _value in group.alternative
                   if (element := standard(group.elements.get(key))) in stored and stored[element]["kpi_key"] != key}
        current = {key for key, _label, _value in group.members}
        other = {renamed[key]["kpi_key"] if key in renamed else key for key, _label, _value in group.alternative}
        if recent and len(other & recent) > len(current & recent):
            def rekey(values: dict) -> dict:
                return {renamed[key]["kpi_key"] if key in renamed else key: value for key, value in values.items()}
            members = [(renamed[key]["kpi_key"], renamed[key]["kpi_label"], value) if key in renamed else (key, label, value)
                       for key, label, value in group.alternative]
            return replace(group, members=members, alternative=group.members, elements=rekey(group.elements),
                           prior=rekey(group.prior), year_to_date=rekey(group.year_to_date),
                           prior_year_to_date=rekey(group.prior_year_to_date))
        return group
