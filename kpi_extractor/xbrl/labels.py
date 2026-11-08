"""Names and dates from an XBRL instance: members' local names and labels, the member tree, and the fiscal year."""

from __future__ import annotations

from datetime import date
from typing import Callable
import pandas as pd
import re

from .model import MAX_RECONCILIATION_ERROR


def local_name(qname: str) -> str:
    return qname.split(":", 1)[-1] if qname else qname


def _axis_name(column: str) -> str:
    """ "dim_us-gaap_StatementBusinessSegmentsAxis" → "StatementBusinessSegmentsAxis"."""
    match = re.match(r"^dim_[^_]+_(.+)$", column)
    return match.group(1) if match else column[4:]


def humanize(name: str) -> str:
    """ "ChinaIncludingHongKongMember" → "China Including Hong Kong"."""
    base = re.sub(r"(SegmentMember|Member|Axis)$", "", local_name(name))
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", base)
    return words.strip() or base


def official_labels(element_catalog: dict | None) -> Callable[[str], str | None]:
    """The filing's own label for an axis or member ("Americas" for aapl:AmericasSegmentMember), terse label first."""
    catalog = element_catalog or {}

    def label(qname: str) -> str | None:
        element = catalog.get(qname.replace(":", "_"))
        labels = getattr(element, "labels", None) or {}
        for role, text in sorted(labels.items(), key=lambda item: (not item[0].endswith("terseLabel"), not item[0].endswith("/label"))):
            if role.endswith(("terseLabel", "/label")) and text:
                cleaned = re.sub(r"\s*\[(member|axis|domain|line items|table)\]\s*$", "", str(text), flags=re.I).strip()
                if cleaned:
                    return cleaned
        return None

    return label


def presented_labels(element_catalog: dict | None) -> Callable[[str], str | None]:
    """A line item's name as its table presents it: Palantir's "General and administrative expenses" is the verbose
    label of a concept whose standard label is "General and Administrative Expense Excluding Stockbased Compensation
    Expense". The standard label only when the filing gives no other."""
    catalog = element_catalog or {}

    def rank(role: str) -> int:
        return 0 if role.endswith("terseLabel") else 2 if role.endswith("/label") else 1

    def label(qname: str) -> str | None:
        element = catalog.get(qname.replace(":", "_"))
        labels = getattr(element, "labels", None) or {}
        shown = [(rank(role), str(text).strip()) for role, text in labels.items()
                 if text and role.endswith(("Label", "/label")) and not role.endswith(("periodStartLabel", "periodEndLabel"))]
        return min(shown, key=lambda item: item[0])[1] if shown else None

    return label


def member_children(xbrl) -> dict[str, set[str]]:
    """Each member's declared children (unh:TotalOptumMember → Optum Health, Optum Insight, Optum Rx), from the filing's
    presentation trees and its definition domains: what the company says a member is made of."""
    children: dict[str, set[str]] = {}

    def qname(element_id: str) -> str:
        return element_id.replace("_", ":", 1)

    for tree in (getattr(getattr(xbrl, "parser", None), "presentation_trees", None) or {}).values():
        for node_id, node in tree.all_nodes.items():
            kids = [child for child in node.children if child.endswith("Member")]
            if node_id.endswith("Member") and kids:
                children.setdefault(qname(node_id), set()).update(qname(child) for child in kids)
    for domains in (getattr(xbrl, "domains_by_role", None) or {}).values():
        for element_id, domain in domains.items():
            kids = [member for member in domain.members if member.endswith("Member")]
            if element_id.endswith("Member") and kids:
                children.setdefault(qname(element_id), set()).update(qname(member) for member in kids)
    return children


def _descendants(member: str, children: dict[str, set[str]]) -> set[str]:
    found, stack = set(), list(children.get(member, ()))
    while stack:
        child = stack.pop()
        if child not in found:
            found.add(child)
            stack.extend(children.get(child, ()))
    return found


def drop_declared_parents(members: dict[str, float], total: float | None, children: dict[str, set[str]]) -> dict[str, float]:
    """Members the filing declares as parents of at least two members also present (UnitedHealth's "Total Optum" over
    its three Optum segments, net of their internal sales so not their sum) are subtotals, not parts. Parents that
    together make a complete split of revenue (Product and Service beside the product lines) are a coarser split and
    stay, for the two-split logic to keep apart."""
    parents = {member for member in members if len(_descendants(member, children) & set(members)) >= 2}
    if not parents:
        return members
    if total and len(parents) >= 2 and abs(sum(members[p] for p in parents) - total) <= abs(total) * MAX_RECONCILIATION_ERROR:
        return members
    return {member: value for member, value in members.items() if member not in parents}


def _member_labels(facts: pd.DataFrame) -> dict[str, str]:
    labels: dict[str, str] = {}
    if "member" in facts.columns and "dimension_member_label" in facts.columns:
        pairs = facts[["member", "dimension_member_label"]].dropna().drop_duplicates("member")
        for member, label in pairs.itertuples(index=False):
            labels[str(member)] = str(label).strip()
    return labels


def _fiscal_year(entity: dict) -> str | None:
    value = entity.get("fiscal_year")
    return str(value) if value else None


def _as_date(value) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


_SMALL_WORDS = {"and", "of", "or", "the", "by", "for", "in", "to"}


def _title(text: str) -> str:
    """ "markets of customers" → "Markets of Customers"; acronyms and brand casing (HPC, iPhone) stay as written."""
    words = text.split()
    return " ".join(word if not word.islower() else word if index and word in _SMALL_WORDS else word[:1].upper() + word[1:]
                    for index, word in enumerate(words))


def _snake(name: str) -> str:
    base = re.sub(r"Axis$", "", name)
    return re.sub(r"(?<!^)(?=[A-Z])", "_", base).lower()
