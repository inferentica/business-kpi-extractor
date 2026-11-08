"""How close two printed sums must be: printed figures are exact to one unit of their scale, so a sum of n of
them can be off by half a unit for each."""
from __future__ import annotations


def close(left: float, right: float, terms: int = 2) -> bool:
    return abs(left - right) <= 0.5 * terms + 1e-9
