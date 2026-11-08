"""The prompts, one module per task, and what each call is told: its task, its data and its version."""

from __future__ import annotations

import hashlib

from .classify import CLASSIFY_TASK
from .common import SYSTEM
from .curate import CURATE_TASK
from .explain import EXPLAIN_TASK
from .locate import LOCATE_TASK
from .maintain import MAINTAIN_TASK
from .propose import PROPOSE_TASK
from .review import REVIEW_TASK
from .select import SELECT_TASK
from .tags import TAG_TASK
from ..schemas import Spec


_TASKS = {"propose": PROPOSE_TASK, "maintain": MAINTAIN_TASK, "locate": LOCATE_TASK, "review": REVIEW_TASK,
          "classify": CLASSIFY_TASK, "select": SELECT_TASK, "curate": CURATE_TASK, "explain": EXPLAIN_TASK, "tags": TAG_TASK}


def prompt_version(purpose: str) -> str:
    """The version of a task's prompt: a hash of the system message and the task's instructions. Stored with each call,
    so a change of accuracy can be traced to a change of prompt and the figures it produced found again."""
    return hashlib.sha1((SYSTEM + "\n" + _TASKS.get(purpose, purpose)).encode()).hexdigest()[:12]


def propose_data(covered: list[str], to_name: list[str], current: "Spec | None" = None, industry: str = "") -> str:
    covered_text = "\n".join(f"- {label}" for label in covered) or "- none"
    naming_text = "\n".join(to_name) or "- none"
    current_text = ("" if current is None or not current.groups else
                    "\n\nCurrent list (keep every group and key the documents still report; improve labels and add "
                    f"what is missing, but never rename a key):\n{kpi_lines(current)}")
    return ((f"{industry}\n\n" if industry else "") + f"Already covered by XBRL (do not repeat):\n{covered_text}\n\n"
            f"Name these XBRL breakdowns (key: current title; member key = current label):\n{naming_text}{current_text}")


def kpi_lines(spec: Spec, hints: dict[str, str] | None = None) -> str:
    lines = []
    for group in spec.groups:
        for kpi in group.kpis:
            key = f"{group.key}.{kpi.key}"
            hint = f" (last quarter: {hints[key]})" if hints and key in hints else ""
            part_of = next((f" (a line of {other.label})" for other in group.kpis if other.key == kpi.parent), "")
            lines.append(f"- {key}: {group.label} / {kpi.label} [{kpi.unit}]{part_of}{hint}")
    return "\n".join(lines)


def locate_data(period_hint: str, spec: Spec, hints: dict[str, str]) -> str:
    return f"Reported quarter: {period_hint}\nKPIs:\n{kpi_lines(spec, hints)}"
