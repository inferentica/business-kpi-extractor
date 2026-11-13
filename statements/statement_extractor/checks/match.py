"""The first printed line a pattern names."""
from __future__ import annotations


def first_match(figures, pattern, after: int = -1) -> tuple[int, float] | None:
    for index, (label, _level, value) in enumerate(figures):
        if index > after and pattern.search(label):
            return index, value
    return None
