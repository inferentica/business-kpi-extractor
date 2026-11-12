"""The company each auditors' report in a document names, and where the report stands: an annual report can carry
another company's audited statements after its own. FEMSA's 20-Fs carry Heineken's, its equity investee's, opened by
their auditors' report ("We have audited the accompanying consolidated statements of financial position of Heineken
N.V. and subsidiaries as of December 31, 2020 and 2019") and headed with no company name at all ("Consolidated
Statement of Financial Position As at 31 December In millions of €")."""
from __future__ import annotations

import re

from ..printed.tables import clean

# The words before the company: what was audited ("the accompanying consolidated statements of financial position").
# The name starts with a capital and ends before "and subsidiaries", "(the Company)", "as of" or "at".
_REPORT = re.compile(r"(?i:\bwe\s+have\s+audited\s+(?:the\s+)?(?:accompanying\s+)?)(?:[\w,’'\-]+\s+){1,20}?(?i:of)\s+"
                     r"(?P<name>[A-Z][\w.,&’'\-]*(?:\s+[\w.,&’'\-]+){0,8}?),?\s+"
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
