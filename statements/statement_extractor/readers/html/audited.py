"""The company each auditors' report in a document names, and where the report stands: an annual report can carry
another company's audited statements after its own. FEMSA's 20-Fs carry Heineken's, its equity investee's, opened by
their auditors' report ("We have audited the accompanying consolidated statements of financial position of Heineken
N.V. and subsidiaries as of December 31, 2020 and 2019") and headed with no company name at all ("Consolidated
Statement of Financial Position As at 31 December In millions of €"). The first run of reports in a document opens the
registrant's own statements, whatever companies they name: CEMIG's 20-Fs print, before its statements, the reports its
auditors relied on for its investees ("We have audited the balance sheet of Norte Energia S.A."). Only a report after
statements opens another company's."""
from __future__ import annotations

import re

from ..printed.tables import clean

# The words before the company: what was audited ("the accompanying consolidated statements of financial position").
# The name starts with a capital and ends before "and subsidiaries", "(the Company)", "as of" or "at".
# The name never starts with a statement's own words ("statements of Financial Position of KT Corporation"); it may
# hold a dash ("Companhia Energética de Minas Gerais—CEMIG").
_STATEMENT_WORDS = r"(?!(?i:financial\s+position|operations|income|cash\s+flows?|comprehensive|changes|earnings|stockholders|shareholders)\b)"
_REPORT = re.compile(r"(?i:\bwe\s+have\s+audited\s+(?:the\s+)?(?:accompanying\s+)?)(?:[\w,’'\-]+\s+){1,20}?(?i:of)\s+"
                     + _STATEMENT_WORDS + r"(?P<name>[A-Z][\w.,&’'\-–—]*(?:\s+[\w.,&’'\-–—]+){0,8}?),?\s+"
                     r"(?=(?i:and\s+(?:its\s+)?(?:consolidated\s+)?subsidiar)|\(|(?i:as\s+(?:of|at)\b)|(?i:at\s+(?:December|June|March|September)))")


def audit_reports(texts) -> list[tuple[int, str]]:
    """Each auditors' report's place in document order and the company it names, from (place, text) pieces."""
    found, window = [], []
    for order, piece in texts:
        if piece and piece.strip():
            window = (window + [clean(piece)])[-10:]
            match = _REPORT.search(" ".join(window))
            if match:
                found.append((order, match.group("name").strip(" ,")))
                window = []
    return found


def audited_names(reports: list[tuple[int, str]], tables: list[tuple[int, bool]]) -> list[str]:
    """Each table's audited company, from the reports' places and each table's place and whether it prints figures:
    "" under the document's first run of reports (the registrant's own), else the first company named by the last run
    of reports before it. A run is reports with no table of figures between them."""
    runs: list[tuple[int, str]] = []  # each run's first report: its place and company
    previous = None
    for place, name in reports:
        if previous is None or any(previous < table < place and figures for table, figures in tables):
            runs.append((place, name))
        previous = place
    names = []
    for table, _figures in tables:
        run = max((index for index, (place, _name) in enumerate(runs) if place <= table), default=-1)
        names.append(runs[run][1] if run > 0 else "")
    return names
