"""Registrants a company's reporting passed from: their filings are the company's own up to its own first filing.

Embraer S.A. (CIK 1355444) became the group's registrant in its 2006 restructuring; until then Embraer - Empresa
Brasileira de Aeronáutica (CIK 1117603, "EMBRAER BRAZILIAN AVIATION CO" on EDGAR) filed its 20-Fs from FY2001 and its
6-Ks from February 2002. Each CIK here was checked against EDGAR's company listing by hand."""
from __future__ import annotations

PREDECESSORS: dict[str, tuple[int, ...]] = {"EMBJ": (1117603,)}


def predecessor_companies(symbol: str) -> list:
    """The company's predecessors on EDGAR, newest first."""
    from edgar import Company
    return [Company(cik) for cik in PREDECESSORS.get(symbol.upper(), ())]
