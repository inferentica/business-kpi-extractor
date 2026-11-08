"""Row labels as compared across a filing's presentations."""
from __future__ import annotations

import re


def label_key(label: str) -> str:
    """A label without case, note marks or punctuation: "Total credit losses recognized in income (a)" and "total
    credit losses recognized in income" are one line."""
    label = re.sub(r"\((\w|\d{1,2})\)", " ", label.lower())
    return re.sub(r"[^a-z0-9]+", " ", label).strip()
