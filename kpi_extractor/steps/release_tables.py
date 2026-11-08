"""Figures read from earnings releases' tables with no AI: breakdowns, quarter tables and repeated rows."""
from __future__ import annotations

import re
from datetime import date

from ..documents.document import clean
from ..domain.fiscal import fiscal_label
from ..readers.release_breakdowns import release_breakdown
from ..readers.release_columns import amount, earlier_quarters, quarter_table_rows, release_grids, row_key, row_numbers
from ..readers.release_statements import release_headline, release_statements
from ..sources.sec import FilingRef
from ..readers.segment_income import GROUP_KEY as INCOME_GROUP
from .shared import CHART_METHOD

def _row_label(locator: dict) -> str | None:
    """The name a reading's row goes by, its footnote mark dropped: "Lithography systems sold (units) 1 132 77 …" is
    "Lithography systems sold (units)"."""
    label = locator.get("row_label") or (re.split(r"\s\$?\(?[\d,.]+\)?(?:\s|$)", clean(locator["quote"]), maxsplit=1)[0]
                                         if locator.get("quote") else None)
    label = re.sub(r"(?:\s+\d{1,2})+$", "", clean(label or ""))
    return label if len(label) >= 8 else None


def own_label(ref: FilingRef, own: dict) -> str:
    quarter = next((quarter for quarter, other in own.items() if other is ref), None)
    return f"{quarter[1]} {quarter[0]}" if quarter else ref.accession



class ReleaseTableReading:
    """Figures read from earnings releases' tables with no AI: breakdowns, quarter tables and repeated rows."""

    def _read_release_breakdowns(self, releases: list[FilingRef]) -> list[dict]:
        """The newest quarter's segment and product revenue from its earnings release, while its report is not filed
        (Alphabet's 10-Q follows its release by days): each breakdown the reports gave last quarter, its lines found in
        a table of the release by name and checked against the year-ago column and total revenue. The report's XBRL
        replaces them; a breakdown the release does not give (Alphabet's regions) waits for the report."""
        latest = max((ref for ref in releases if ref.period_end), key=lambda item: item.filed, default=None)
        if latest is None:
            return []
        try:
            own = fiscal_label(latest.period_end, self.profile.fiscal_year_end, year_offset=self.offset)
            live = [value for value in self.values.values() if value["validation_status"] == "verified"
                    and value["fiscal_period"] != "FY" and value["unit"] == "currency"]
            if any(value["method"] == "xbrl" and (value["fiscal_year"], value["fiscal_period"]) == own for value in live):
                return []  # the report is in
            stored = {value["group_key"] for value in live if (value["fiscal_year"], value["fiscal_period"]) == own
                      and (value.get("locator") or {}).get("release_table") and value["source_accession"] == latest.accession}
            reported = [value for value in live if value["method"] in ("xbrl", "derived")
                        and (value["fiscal_year"], value["fiscal_period"]) < own]
            if not reported:
                return []
            before = max((value["fiscal_year"], value["fiscal_period"]) for value in reported)
            year_ago = (str(int(own[0]) - 1), own[1])
            groups: dict[str, list[dict]] = {}
            for value in reported:
                if (value["fiscal_year"], value["fiscal_period"]) == before:
                    groups.setdefault(value["group_key"], []).append(value)
            past = {(value["group_key"], value["kpi_key"]): float(value["value"]) for value in reported
                    if (value["fiscal_year"], value["fiscal_period"]) == year_ago}
            document = self._document(latest)
            records: list[dict] = []
            tables: dict[str, str] = {}
            # Subtotal groups ("…_levels") last: their lines are looked for in the table their breakdown came from.
            for key in sorted(groups, key=lambda item: item.endswith("_levels")):
                if key in stored:
                    continue
                lines = groups[key]
                levels = key.endswith("_levels")
                within = [tables[key.removesuffix("_levels")]] if levels and key.removesuffix("_levels") in tables else None
                if levels and within is None:
                    continue
                found = release_breakdown(document, {line["kpi_key"]: line["kpi_label"] for line in lines},
                                          {line["kpi_key"]: past[(key, line["kpi_key"])] for line in lines
                                           if (key, line["kpi_key"]) in past},
                                          latest.period_end, tables=within, totalled=not levels,
                                          # Segment operating income from the same table's "Operating income" rows.
                                          measure="income" if key == INCOME_GROUP else "revenue")
                if found is None:
                    continue
                tables[key] = found.table
                total = None if levels else found.total
                gap = round((sum(found.values.values()) - total) / total * 100, 4) if total else None
                for line in lines:
                    member = line["kpi_key"]
                    parent = (line.get("locator") or {}).get("parent")
                    records.append(self._record(
                        group_key=key, group_label=line["group_label"], group_kind=line["group_kind"],
                        group_order=line["group_order"], kpi_key=member, kpi_label=line["kpi_label"],
                        kpi_order=line["kpi_order"], unit="currency", value=found.values[member],
                        currency=line.get("currency"), method=CHART_METHOD, validation_status="verified",
                        reconciliation_error_pct=gap, notes=["from the earnings release; the quarter's report replaces it"],
                        ref=latest, fiscal_year=own[0], fiscal_period=own[1], period_end=latest.period_end,
                        locator={"release_table": found.table, "row": found.rows[member],
                                 **({"total": total} if total is not None else {}),
                                 **({"subtotal": True} if levels else {}),
                                 **({"parent": parent} if parent else {}),
                                 **({"prior": found.prior[member]} if member in found.prior else {})},
                    ))
            if records:
                self.log(f"{self.symbol}: {latest.form} {latest.filed} release breakdowns read ({len(records)} values)")
            return records
        except Exception as error:  # noqa: BLE001
            self.log(f"{self.symbol}: breakdowns of {latest.form} {latest.filed} not read ({type(error).__name__}: {error})"[:300])
            return []

    def _fill_quarter_tables(self, releases: list[FilingRef]) -> list[dict]:
        """A metric's missing quarters from the quarter-headed tables releases carry (ASML's slides give five quarters of
        lithography systems sold; its KPI, added later, was read only from the latest releases). A row is found by the
        names the KPI's readings give it and taken only when another of its columns equals a figure already held, which
        also gives its scale; each quarter filled this way vouches for the next table. A quarter's own release is
        preferred; one held in any way is never replaced, and quarters before the KPI's first are not looked for."""
        held: dict[tuple[str, str], dict[tuple[str, str], dict]] = {}
        for v in self.values.values():
            if v["group_kind"] == "metric" and v["validation_status"] != "rejected" and v["fiscal_period"] != "FY":
                held.setdefault((v["group_key"], v["kpi_key"]), {})[(v["fiscal_year"], v["fiscal_period"])] = v
        own = {}
        for ref in releases:
            quarter = self._quarter_of(ref.period_end) if ref.period_end else None
            if quarter and quarter[1] != "FY":
                own.setdefault(quarter, ref)
        index = lambda quarter: int(quarter[0]) * 4 + int(quarter[1][1])  # noqa: E731
        wanted: dict[tuple[str, str], tuple[list[str], set[tuple[str, str]]]] = {}
        for kpi, rows in held.items():
            verified = [v for v in rows.values() if v["validation_status"] == "verified" and v["method"] == "ai"]
            labels = {label for v in verified if (label := _row_label(v.get("locator") or {}))}
            first = min((index(quarter) for quarter in rows), default=None)
            gaps = {quarter for quarter in own if first is not None and index(quarter) > first and quarter not in rows}
            if labels and gaps:
                wanted[kpi] = (sorted(labels), gaps)
        if not wanted:
            return []
        reach = {quarter for _labels, gaps in wanted.values() for gap in gaps for quarter in own
                 if 0 <= index(quarter) - index(gap) <= 4}
        texts: dict[str, list[str]] = {}
        for quarter in sorted(reach)[-40:]:
            ref = own[quarter]
            try:
                document = self._document(ref)
            except Exception:  # noqa: BLE001 - an unreadable release gives nothing
                continue
            texts[ref.accession] = [" ".join(" ".join(row) for row in document.tables[key].rows) if key in document.tables
                                    else document.blocks[key].text for key in document.order]
        records = []
        for kpi, (labels, gaps) in wanted.items():
            known = {quarter: float(v["value"]) for quarter, v in held[kpi].items() if v["validation_status"] == "verified"}
            found: dict[tuple[str, str], tuple[float, FilingRef]] = {}
            for _pass in range(3):  # a gap a table fills proves the next table
                before = len(found)
                for quarter in sorted(reach):
                    ref = own[quarter]
                    for text in texts.get(ref.accession, []):
                        for label in labels:
                            for row in quarter_table_rows(text, label):
                                figures = {(str(year), f"Q{number}"): value for (year, number), value in row.items()}
                                overlap = {q: known.get(q) or (found[q][0] if q in found else None) for q in figures}
                                overlap = {q: value for q, value in overlap.items() if value is not None}
                                scales = {next((scale for scale in (1, 1e3, 1e6, 1e9)
                                                if figures[q] and abs(value / (figures[q] * scale) - 1) < 1e-4), None)
                                          for q, value in overlap.items()}
                                if len(scales) != 1 or None in scales:
                                    continue
                                scale = scales.pop()
                                for gap in gaps & set(figures):
                                    if gap not in found or own.get(gap) is ref:
                                        found[gap] = (figures[gap] * scale, ref)
                if len(found) == before:
                    break
            template = max((v for v in held[kpi].values() if v["validation_status"] == "verified"), key=lambda v: v["period_end"])
            for quarter, (value, ref) in sorted(found.items()):
                end = own[quarter].period_end
                records.append({**template, "fiscal_year": quarter[0], "fiscal_period": quarter[1], "period_end": end.isoformat(),
                                "value": value, "method": "ai", "validation_status": "verified", "reconciliation_error_pct": None,
                                "source_accession": ref.accession, "source_url": ref.source_url, "filed_at": ref.filed.isoformat(),
                                "notes": [f"from the quarterly table of the release for {own_label(ref, own)}"],
                                "locator": {"quarter_table": True, "row": labels[0]}})
        if records:
            self.log(f"{self.symbol}: {len(records)} KPI values from the releases' quarterly tables")
        return records

    def _fill_quoted_rows(self, releases: list[FilingRef]) -> list[dict]:
        """A metric's missing quarters from rows its releases repeat in a fixed layout: TSMC's management report gives
        "Shipment (Kpcs, 12"-equiv. Wafer) 2,916 3,227 3,799" (the quarter, the one before, the year before). The layout
        is learned from the KPI's own readings, a column for a lag only when at least two readings agree with figures
        held and none contradicts. A gap is then filled from a reading's lagged column, or from its own quarter's release
        where that row's lagged columns equal figures held (each quarter filled vouches for the next)."""
        held: dict[tuple[str, str], dict[tuple[str, str], dict]] = {}
        for v in self.values.values():
            if v["group_kind"] == "metric" and v["validation_status"] != "rejected" and v["fiscal_period"] != "FY":
                held.setdefault((v["group_key"], v["kpi_key"]), {})[(v["fiscal_year"], v["fiscal_period"])] = v
        index = lambda quarter: int(quarter[0]) * 4 + int(quarter[1][1]) - 1  # noqa: E731
        label_of = lambda number: (str(number // 4), f"Q{number % 4 + 1}")  # noqa: E731
        refs: dict[tuple[str, str], list[FilingRef]] = {}
        for ref in sorted(releases, key=lambda item: item.filed):
            quarter = self._quarter_of(ref.period_end) if ref.period_end else None
            if quarter and quarter[1] != "FY":
                refs.setdefault(quarter, []).append(ref)
        # Each quarter's end as stored figures give it: a quarter with no release of its own (TSMC's Q4 2022) still has one.
        ends = {(v["fiscal_year"], v["fiscal_period"]): str(v["period_end"])[:10] for v in self.values.values()
                if v["fiscal_period"] != "FY" and v.get("period_end")}
        ends.update({quarter: own[0].period_end.isoformat() for quarter, own in refs.items()})
        records = []
        loaded: dict[str, str] = {}

        def text_of(ref: FilingRef) -> str:
            if ref.accession not in loaded and len(loaded) < 250:
                try:
                    document = self._document(ref)
                    loaded[ref.accession] = " ".join(
                        " ".join(" ".join(row) for row in document.tables[key].rows) if key in document.tables
                        else document.blocks[key].text for key in document.order)
                except Exception:  # noqa: BLE001 - an unreadable release gives nothing
                    loaded[ref.accession] = ""
            return loaded.get(ref.accession, "")

        for kpi, rows in held.items():
            readings = []
            for quarter, v in rows.items():
                label = _row_label(v.get("locator") or {})
                quote = (v.get("locator") or {}).get("quote")
                if v["validation_status"] != "verified" or v["method"] != "ai" or not label or not quote:
                    continue
                numbers = next((found for found in row_numbers(quote, label) if found), None)
                scale = next((item for item in (1, 1e3, 1e6, 1e9) if numbers and numbers[0]
                              and abs(float(v["value"]) / (numbers[0] * item) - 1) < 1e-6), None) if numbers else None
                if scale:
                    readings.append((quarter, label, [n * scale for n in numbers], scale, v))
            known = {quarter: float(v["value"]) for quarter, v in rows.items() if v["validation_status"] == "verified"}
            layout = {}
            for column in (1, 2, 3):
                for lag in (1, 4):
                    agree = contradict = 0
                    for quarter, _label, numbers, _scale, _v in readings:
                        earlier = label_of(index(quarter) - lag)
                        if len(numbers) > column and earlier in known:
                            same = abs(numbers[column] - known[earlier]) <= abs(known[earlier]) * 1e-6
                            agree, contradict = agree + same, contradict + (not same)
                    if agree >= 2 and not contradict:
                        layout[column] = lag
            if not layout:
                continue
            first = min(index(quarter) for quarter in rows)
            lagged: dict[tuple[str, str], tuple[float, str, dict]] = {}
            for quarter, _label, numbers, _scale, reading in readings:
                source = {field: reading.get(field) for field in ("source_accession", "source_url", "filed_at")}
                for column, lag in layout.items():
                    earlier = label_of(index(quarter) - lag)
                    if len(numbers) > column and earlier not in rows and index(earlier) > first and earlier not in lagged:
                        lagged[earlier] = (numbers[column], f"the release for {quarter[1]} {quarter[0]}, its column a "
                                                           f"{'quarter' if lag == 1 else 'year'} before", source)
            labels = sorted({label for _quarter, label, _numbers, _scale, _v in readings})
            scale = readings[0][3] if readings else 1
            # A quarter's own release first; a later release's column where its own does not give the row.
            found: dict[tuple[str, str], tuple[float, str, dict]] = {}
            gaps = sorted((quarter for quarter in ends if index(quarter) > first and quarter not in rows), key=index)
            # A quarter held a quarter or a year after a gap: its release's earlier columns, when its own figure is the
            # one held (TSMC's Q4 2023 report gives Q4 2022 a year back).
            after = {label_of(index(gap) + lag) for gap in gaps for lag in set(layout.values())}
            for quarter in sorted((quarter for quarter in after if quarter in refs and quarter in known), key=index):
                for ref in refs[quarter][:12]:
                    for label in labels:
                        for numbers in row_numbers(text_of(ref), label):
                            if abs(numbers[0] * scale - known[quarter]) > abs(known[quarter]) * 1e-6:
                                continue
                            source = {"source_accession": ref.accession, "source_url": ref.source_url, "filed_at": ref.filed.isoformat()}
                            for column, lag in layout.items():
                                earlier = label_of(index(quarter) - lag)
                                if len(numbers) > column and earlier in gaps and earlier not in lagged:
                                    lagged[earlier] = (numbers[column] * scale, f"the release for {quarter[1]} {quarter[0]}, its "
                                                       f"column a {'quarter' if lag == 1 else 'year'} before", source)
            for quarter in gaps:
                for ref in refs.get(quarter, [])[:12]:
                    for label in labels:
                        for numbers in row_numbers(text_of(ref), label):
                            checks = [(numbers[column] * scale, label_of(index(quarter) - lag)) for column, lag in layout.items()
                                      if len(numbers) > column]
                            figure_of = lambda earlier: known.get(earlier, (found.get(earlier) or lagged.get(earlier) or (None,))[0])  # noqa: E731
                            proven = [(figure, earlier) for figure, earlier in checks if figure_of(earlier) is not None]
                            target = figure_of
                            if proven and all(abs(figure - target(earlier)) <= abs(target(earlier)) * 1e-6 for figure, earlier in proven):
                                source = {"source_accession": ref.accession, "source_url": ref.source_url,
                                          "filed_at": ref.filed.isoformat()}
                                found.setdefault(quarter, (numbers[0] * scale, f"the release for {quarter[1]} {quarter[0]}", source))
                                # Its own earlier columns fill gaps behind it too (Q1 2023's report gives Q4 2022).
                                for figure, earlier in checks:
                                    if earlier not in rows and index(earlier) > first and earlier not in lagged:
                                        lagged[earlier] = (figure, f"the release for {quarter[1]} {quarter[0]}, its column "
                                                                   f"for {earlier[1]} {earlier[0]}", source)
                    if quarter in found:
                        break
            found = {**lagged, **found}
            template = max((v for v in rows.values() if v["validation_status"] == "verified"), key=lambda v: v["period_end"])
            for quarter, (value, where, source) in sorted(found.items()):
                if quarter not in ends:
                    continue
                records.append({**template, **source, "fiscal_year": quarter[0], "fiscal_period": quarter[1], "period_end": ends[quarter],
                                "value": value, "method": "ai", "validation_status": "verified", "reconciliation_error_pct": None,
                                "notes": [f"from {where}"], "locator": {"quoted_row": True, "row": labels[0]}})
        if records:
            self.log(f"{self.symbol}: {len(records)} KPI values from rows the releases repeat")
        return records

    def _fill_release_columns(self, releases: list[FilingRef]) -> list[dict]:
        """A recent listing's releases show earlier quarters beside their own (see release_columns): each KPI read from a
        release and each line of a breakdown its reports tag are taken from those columns for quarters held by nothing
        else, at the scale of the release's own figure. A breakdown's quarter must add up to the revenue another source
        gives for it (its year-ago comparative, the prospectus); one renamed line is the remainder, as in the prospectus."""
        records: list[dict] = []
        groups = self._xbrl_groups()
        for ref in releases:
            if ref.period_end is None:
                continue
            try:
                document = self._document(ref)
            except Exception:  # noqa: BLE001
                continue
            own = fiscal_label(ref.period_end, self.profile.fiscal_year_end, year_offset=self.offset)
            grids = release_grids(document)
            rows = [(grid, label, cells, earlier_quarters(grid, cells, ref.period_end, additive=False),
                     next((amount(cell) for column, cell in zip(grid.columns, cells)
                           if column.months == 3 and abs((column.end - ref.period_end).days) <= 10), None))
                    for grid in grids for label, cells in grid.rows]
            records += self._release_column_kpis(ref, own, rows)
            records += self._release_column_lines(ref, own, rows, groups)
        if records:
            self.log(f"{self.symbol}: {len(records)} values from the earlier quarters the releases show")
        return records

    def _quarter_of(self, end: date) -> tuple[str, str] | None:
        try:
            return fiscal_label(end, self.profile.fiscal_year_end, year_offset=self.offset)
        except Exception:  # noqa: BLE001
            return None

    def _release_column_kpis(self, ref: FilingRef, own: tuple[str, str], rows) -> list[dict]:
        records = []
        for value in [v for v in self.values.values() if v["source_accession"] == ref.accession and v["method"] == "ai"
                      and v["validation_status"] == "verified" and (v["fiscal_year"], v["fiscal_period"]) == own]:
            locator = value.get("locator") or {}
            label = None
            if locator.get("table") in self._document(ref).tables and isinstance(locator.get("row"), int):
                table = self._document(ref).tables[locator["table"]]
                label = clean(table.rows[locator["row"]][0]) if locator["row"] < len(table.rows) else None
            elif locator.get("quote"):
                label = re.split(r"\s\$?\(?[\d,.]+\)?(?:\s|$)", clean(locator["quote"]), maxsplit=1)[0]
            match = next(((grid, cells, earlier, mine) for grid, row_label, cells, earlier, mine in rows
                          if label and row_label == label and mine), None)
            if match is None:
                continue
            grid, cells, _, mine = match
            factor = float(value["value"]) / mine
            scale = next((item for item in (1, 1e3, 1e6, 1e9) if abs(factor / item - 1) < 0.01), None)
            if scale is None:
                continue
            additive = value["unit"] == "currency" and not re.search(r"\bper\b|average", label, re.I)
            for end, figure in earlier_quarters(grid, cells, ref.period_end, additive).items():
                quarter = self._quarter_of(end)
                if quarter is None or any(v["group_key"] == value["group_key"] and v["kpi_key"] == value["kpi_key"]
                                          and (v["fiscal_year"], v["fiscal_period"]) == quarter and v["validation_status"] != "rejected"
                                          for v in self.values.values()):
                    continue
                records.append({**value, "fiscal_year": quarter[0], "fiscal_period": quarter[1], "period_end": end.isoformat(),
                                "value": figure * scale, "notes": [f"an earlier column of the release for {own[1]} {own[0]}"],
                                "locator": {"release_column": end.isoformat(), "row": label, "source": grid.source}})
        return records

    def _release_column_lines(self, ref: FilingRef, own: tuple[str, str], rows, groups: dict[str, list[dict]]) -> list[dict]:
        records = []
        for group, lines in groups.items():
            stored = {v["kpi_key"]: v for v in self.values.values() if v["group_key"] == group and v["method"] == "xbrl"
                      and v["validation_status"] == "verified" and (v["fiscal_year"], v["fiscal_period"]) == own}
            members = {line["kpi_key"]: line for line in lines}
            found: dict[str, tuple] = {}
            for grid, label, cells, _, mine in rows:
                key = row_key(label)
                member = next((m for m, line in members.items() if key in (m, row_key(line["kpi_label"]))), None)
                if member and member not in found:
                    found[member] = (grid, label, cells, mine)
            # The scale: the release's own figure of a line against the report's.
            scale = next((item for member, (_g, _l, _c, mine) in found.items() if mine and member in stored
                          for item in (1, 1e3, 1e6, 1e9) if abs(float(stored[member]["value"]) / mine / item - 1) < 0.01), None)
            if scale is None:
                continue
            additive = {member: earlier_quarters(grid, cells, ref.period_end, True) for member, (grid, _l, cells, _m) in found.items()}
            ends = {end for quarters in additive.values() for end in quarters}
            for end in sorted(ends):
                quarter = self._quarter_of(end)
                if quarter is None or self._held(group, quarter):
                    continue
                total = next((float(t) for v in self.values.values() if (v["fiscal_year"], v["fiscal_period"]) == quarter
                              and v["validation_status"] == "verified" and v["method"] in ("xbrl", "derived")
                              and isinstance(t := (v.get("locator") or {}).get("total"), (int, float))), None)
                if total is None:
                    continue
                values = {member: quarters[end] * scale for member, quarters in additive.items() if end in quarters}
                missing = [member for member in members if member not in values]
                tolerance = 1.5 * scale * len(members)
                if len(missing) == 1:
                    # A renamed line (SpaceX's AI, SI by its next report): the one other row equal to the rest of revenue.
                    rest = total - sum(values.values())
                    candidates = {label: figure for grid, label, cells, _e, _m in rows if row_key(label) not in values
                                  for figure in [earlier_quarters(grid, cells, ref.period_end, True).get(end, float("nan")) * scale]
                                  if abs(figure - rest) <= tolerance}
                    if len(candidates) != 1:
                        continue
                    values[missing[0]] = next(iter(candidates.values()))
                elif missing:
                    continue
                if abs(sum(values.values()) - total) > tolerance:
                    continue
                for member, figure in values.items():
                    line = members[member]
                    records.append({**line, "fiscal_year": quarter[0], "fiscal_period": quarter[1], "period_end": end.isoformat(),
                                    "value": round(figure, 2), "method": CHART_METHOD, "source_accession": ref.accession,
                                    "source_url": ref.source_url, "filed_at": ref.filed.isoformat(), "reconciliation_error_pct": 0,
                                    "notes": [f"an earlier column of the release for {own[1]} {own[0]}"],
                                    "locator": {"release_table": "history", "total": total, "member": (line.get("locator") or {}).get("member")}})
        return records

    def _series_for(self, axis: str) -> str | None:
        """The quarterly revenue series whose members a pie names: the group labelled "Revenue by <axis>" ("Wafer Revenue
        by Technology") among the values read from the quarters' reports (an annual report's XBRL series of that name
        is the year's, not this)."""
        groups = {value["group_key"] for value in self.values.values()
                  if value["validation_status"] == "verified" and value["unit"] == "currency"
                  and value["method"] != "xbrl" and value["fiscal_period"] != "FY"
                  and re.fullmatch(rf"(\w+ )?revenue by {axis}", str(value.get("group_label") or "").lower())}
        return groups.pop() if len(groups) == 1 else None

    def _release_revenue(self, ref: FilingRef) -> tuple[float, str | None] | None:
        """The quarter's net revenue as its release states it: its income statement, else its summary table."""
        document = self._document(ref)
        for statement in release_statements(document):
            if statement.statement == "income" and abs((statement.period_end - ref.period_end).days) <= 3:
                revenue = next((line["value"] for line in statement.lines if line["concept"] == "release_Revenues"), None)
                if revenue:
                    return float(revenue), statement.currency
        headline = release_headline(document, ref.period_end)
        revenue = next((line["value"] for line in headline.lines if line["concept"] == "release_Revenues"), None) if headline else None
        return (float(revenue), headline.currency) if revenue else None
