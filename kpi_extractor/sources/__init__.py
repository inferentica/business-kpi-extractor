"""Where filings come from: each source behind one adapter (base.FilingSource), and the filing archive."""
from __future__ import annotations

from .base import FilingSource


def sources() -> dict[str, FilingSource]:
    """Every source by name. SEC EDGAR is the only one so far."""
    from .sec import SEC
    return {SEC.name: SEC}


def source(name: str = "sec") -> FilingSource:
    return sources()[name]


def accepts_url(url) -> bool:
    """Whether a value's source address is one of a source's own documents (or none is given)."""
    return url is None or any(str(url).startswith(prefix) for found in sources().values() for prefix in found.url_prefixes)
