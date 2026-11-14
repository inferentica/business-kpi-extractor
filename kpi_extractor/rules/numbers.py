"""When two figures are the same number."""
from __future__ import annotations


def same_figure(left: float, right: float) -> bool:
    """The same figure, to the float noise of arithmetic on it (a billion's last digit, never a rounding)."""
    return abs(left - right) <= 1e-9 * max(1.0, abs(left), abs(right))
