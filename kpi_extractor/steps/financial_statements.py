"""The three financial statements, from XBRL or, for a filer without quarterly XBRL, from its releases."""
from __future__ import annotations


from ..fiscal import fiscal_label
from ..release_statements import release_headline, release_statements
from ..sec import FilingRef
from ..statements import statement_lines

class StatementReading:
    """The three financial statements, from XBRL or, for a filer without quarterly XBRL, from its releases."""

    def _store_statements(self, ref: FilingRef, xbrl, breakdowns, fiscal_year: str, fiscal_period: str) -> None:
        """The filing's three statements as presented; a statement that cannot be read never holds up its KPIs."""
        try:
            lines, currency = statement_lines(xbrl, breakdowns.period_end, breakdowns.annual)
            if not lines:
                return
            currency = currency or next((group.currency for group in breakdowns.groups if group.currency), "USD")
            shared = {"fiscal_year": fiscal_year, "fiscal_period": fiscal_period,
                      "period_end": breakdowns.period_end.isoformat(), "form": ref.form,
                      "filed_at": ref.filed.isoformat(), "currency": currency}
            self.store.statements(ref.accession, [{**line, **shared} for line in lines])
        except Exception as error:  # noqa: BLE001
            self.log(f"{self.symbol}: statements of {ref.form} {ref.filed} not stored ({type(error).__name__}: {error})"[:300])

    def _no_quarterly_reports(self, reports: list[FilingRef]) -> bool:
        return bool(reports) and not any(ref.form.upper().startswith("10-Q") for ref in reports)

    def _store_release_statements(self, reports: list[FilingRef], releases: list[FilingRef]) -> None:
        """Quarterly statements for a filer whose only XBRL is annual (a 20-F filer such as ASML): read from its
        earnings releases' own tables, no AI. Each quarter is in several releases (a five-quarter summary), so it is
        kept from the newest release that presents it; a release whose own quarter a newer one already gave is not
        even opened. A release that cannot be read never holds up the KPIs."""
        if not self._no_quarterly_reports(reports):
            return
        owners = self._statement_owners
        for ref in sorted(releases, key=lambda item: item.filed, reverse=True):
            if ref.period_end is None:
                continue
            try:
                own = fiscal_label(ref.period_end, self.profile.fiscal_year_end, year_offset=self.offset)
                if any(held["accession"] == ref.accession and key[0] == "income" for key, held in owners.items()):
                    continue  # already read whole: it owns an income statement (an annual report's is its year)
                owner = owners.get(("income", *own, "quarter"))
                if owner and str(owner.get("filed_at") or "")[:10] >= ref.filed.isoformat():
                    continue
                lines: list[dict] = []
                for statement in release_statements(self._document(ref)):
                    if statement.currency is None:
                        continue  # amounts in more than one currency side by side (an offering document): not guessed
                    fiscal_year, fiscal_period = fiscal_label(statement.period_end, self.profile.fiscal_year_end,
                                                              year_offset=self.offset)
                    key = (statement.statement, fiscal_year, fiscal_period, statement.duration)
                    held = owners.get(key)
                    if held and held["accession"] != ref.accession and str(held.get("filed_at") or "")[:10] > ref.filed.isoformat():
                        continue  # a newer release already gave this quarter
                    shared = {"statement": statement.statement, "duration": statement.duration, "fiscal_year": fiscal_year,
                              "fiscal_period": fiscal_period, "period_end": statement.period_end.isoformat(),
                              "form": ref.form, "filed_at": ref.filed.isoformat(), "currency": statement.currency}
                    lines.extend({**line, **shared} for line in statement.lines)
                    owners[key] = {"accession": ref.accession, "filed_at": ref.filed.isoformat()}
                if lines:
                    self.store.statements(ref.accession, lines)
                    self.log(f"{self.symbol}: {ref.form} {ref.filed} statements stored ({len(lines)} lines)")
            except Exception as error:  # noqa: BLE001
                self.log(f"{self.symbol}: statements of {ref.form} {ref.filed} not read ({type(error).__name__}: {error})"[:300])
        self._store_headline(releases)

    def _store_headline(self, releases: list[FilingRef]) -> None:
        """The newest quarter's selected income lines from its earnings release (TSMC's summary table), while that
        quarter has no full income statement yet: its consolidated report follows weeks later and replaces them."""
        latest = max((ref for ref in releases if ref.period_end), key=lambda item: item.filed, default=None)
        if latest is None:
            return
        try:
            own = fiscal_label(latest.period_end, self.profile.fiscal_year_end, year_offset=self.offset)
            owners = self._statement_owners
            if any(owners.get(("income", *own, duration)) for duration in ("quarter", "ytd", "year")):
                return
            if (owners.get(("income", *own, "headline")) or {}).get("accession") == latest.accession:
                return
            statement = release_headline(self._document(latest), latest.period_end)
            if statement is None:
                return
            shared = {"statement": "income", "duration": "headline", "fiscal_year": own[0], "fiscal_period": own[1],
                      "period_end": statement.period_end.isoformat(), "form": latest.form,
                      "filed_at": latest.filed.isoformat(), "currency": statement.currency}
            self.store.statements(latest.accession, [{**line, **shared} for line in statement.lines])
            owners[("income", *own, "headline")] = {"accession": latest.accession, "filed_at": latest.filed.isoformat()}
            self.log(f"{self.symbol}: {latest.form} {latest.filed} headline figures stored ({len(statement.lines)} lines)")
        except Exception as error:  # noqa: BLE001
            self.log(f"{self.symbol}: headline of {latest.form} {latest.filed} not read ({type(error).__name__}: {error})"[:300])
