"""What a reader returns: one statement for one period, as one filing presents it, with the checks it passed."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date


STATEMENTS = ("income", "balance", "cash_flow")


# quarter: three months; ytd: the year to date (six or nine months); year: a fiscal year; instant: a balance sheet date.
DURATIONS = ("quarter", "ytd", "year", "instant")


PASS, FAIL, NONE = "pass", "fail", "none"  # a check held, failed, or had nothing it could test


# Home regulators' own data (pipeline/regulators.py), apart from the SEC's filings: its lines are picked out by code
# and tag, the SEC's by printed label, so the two are not yet compared as witnesses of one another.
REGULATOR_SOURCES = ("cvm", "dart", "bmv", "cmf")


@dataclass
class Check:
    name: str  # balance | cash | calc
    result: str  # pass | fail | none
    detail: str = ""


@dataclass
class Reading:
    statement: str
    duration: str
    fiscal_year: str
    fiscal_period: str  # Q1..Q4 or FY
    period_end: date
    period_start: date | None
    currency: str | None
    accession: str
    form: str
    filed: date
    own: bool  # the filing's own period; False for a comparative column (an earlier period the filing presents again)
    source: str  # xbrl, html, text
    basis: str | None = None  # the accounting basis: US GAAP, IFRS, ROC GAAP, Taiwan-IFRS (domain/basis.py)
    scope: str = "consolidated"  # or "parent": the company alone (TSMC's quarterly reports before 2008)
    lines: list[dict] = field(default_factory=list)  # line, concept, label, level, is_heading, value
    checks: list[Check] = field(default_factory=list)
    titled: str | None = None  # the company a printed statement's title names ("… and subsidiaries"), for the pipeline
    # A regulator's template printed in a filing (readers/printed/mexico_rendered.py): its own sign convention, costs
    # as positive amounts.
    template: bool = False
    release_of: str = ""  # the issuer a release opening named before the statement (pipeline/printed.py)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.statement, self.fiscal_year, self.fiscal_period, self.duration

    @property
    def status(self) -> str:
        """kept: its own arithmetic holds; rejected: a check failed; unproven: nothing could be checked."""
        results = [check.result for check in self.checks]
        if FAIL in results:
            return "rejected"
        return "kept" if self._proven(results) else "unproven"

    def _proven(self, results: list[str]) -> bool:
        by_name = {check.name: check.result for check in self.checks}
        if self.statement == "balance":
            return by_name.get("balance") == PASS
        if self.statement == "cash_flow":
            return by_name.get("cash") == PASS
        return by_name.get("calc") == PASS

    def to_json(self, with_lines: bool = True) -> dict:
        data = asdict(self)
        data["period_end"] = self.period_end.isoformat()
        data["period_start"] = self.period_start.isoformat() if self.period_start else None
        data["filed"] = self.filed.isoformat()
        data["status"] = self.status
        data.pop("titled"), data.pop("release_of"), data.pop("template")
        if not with_lines:
            data.pop("lines")
            data["line_count"] = len(self.lines)
        return data
