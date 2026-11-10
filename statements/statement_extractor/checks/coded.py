"""Checks on statements whose lines carry their regulator's account code or IFRS tag. A code says where a line sits:
CVM's chart has each account's sub-accounts sum to it ("1.01" over "1.01.01" and "1.01.02") and fixes the order of
the income statement's results; an IFRS tag names the same totals in every company's report. Each check passes, fails
or has nothing to test, as the printed checks do."""
from __future__ import annotations

from ..domain.model import FAIL, NONE, PASS, Check


def _close(left: float, right: float, unit: float, parts: int = 1) -> bool:
    return abs(left - right) <= unit * max(1, parts)  # each part rounded to the unit it is filed in


def _children(values: dict[str, float], code: str) -> list[str]:
    depth = code.count(".") + 1
    return [other for other in values if other.startswith(code + ".") and other.count(".") == depth]


def cvm_checks(statement: str, values: dict[str, float], unit: float, fixed: set[str] | None = None) -> list[Check]:
    """CVM's account tree: every account with sub-accounts equals their sum (earnings per share, 3.99, are amounts a
    share and sum to nothing); the income statement's results in the chart's order (3.03 = 3.01 + 3.02 … 3.11 =
    3.09 + 3.10); total assets equal total liabilities and equity (1 = 2); the cash flows' sections add to the change
    in cash (6.05 = 6.01 + 6.02 + 6.03 + 6.04), which is closing less opening cash (6.05.02 - 6.05.01).
    Only CVM's fixed accounts are summed: a company's own detail beneath one may be partial (Copel's 2017 intangible
    assets list some concessions), and sub-accounts filed as zeros are a breakdown not given. 6.05's sub-accounts are
    the opening and closing cash, no parts of it."""
    wrong, tested = [], 0
    for code, value in values.items():
        parts = _children(values, code)
        if fixed is not None:
            parts = [part for part in parts if part in fixed] if all(part in fixed for part in parts) else []
        if not parts or code.startswith("3.99") or code == "6.05" or all(values[part] == 0 for part in parts):
            continue
        tested += 1
        total = sum(values[part] for part in parts)
        if not _close(value, total, unit, len(parts)):
            wrong.append(f"{code} {value:,.0f} != {total:,.0f}")
    checks = []
    if statement == "income":
        for result, (left, right) in {"3.03": ("3.01", "3.02"), "3.05": ("3.03", "3.04"), "3.07": ("3.05", "3.06"),
                                      "3.09": ("3.07", "3.08"), "3.11": ("3.09", "3.10")}.items():
            if result in values and left in values and right in values:
                tested += 1
                if not _close(values[result], values[left] + values[right], unit, 2):
                    wrong.append(f"{result} != {left} + {right}")
        checks.append(Check("calc", FAIL if wrong else PASS if tested else NONE, "; ".join(wrong)[:200]))
    elif statement == "balance":
        checks.append(Check("calc", FAIL if wrong else PASS if tested else NONE, "; ".join(wrong)[:200]))
        if "1" in values and "2" in values:
            same = _close(values["1"], values["2"], unit)
            checks.append(Check("balance", PASS if same else FAIL, "" if same else f"1 {values['1']:,.0f} != 2 {values['2']:,.0f}"))
        else:
            checks.append(Check("balance", NONE))
    else:
        sections = [code for code in ("6.01", "6.02", "6.03", "6.04") if code in values]
        detail = list(wrong)
        if "6.05" in values and {"6.01", "6.02", "6.03"} <= set(sections):
            if not _close(values["6.05"], sum(values[code] for code in sections), unit, len(sections)):
                detail.append("6.05 != 6.01 + 6.02 + 6.03 + 6.04")
            if "6.05.01" in values and "6.05.02" in values and not _close(values["6.05.02"] - values["6.05.01"], values["6.05"], unit, 2):
                detail.append("6.05.02 - 6.05.01 != 6.05")
            checks.append(Check("cash", FAIL if detail else PASS, "; ".join(detail)[:200]))
        else:
            checks.append(Check("cash", FAIL if wrong else NONE, "; ".join(wrong)[:200]))
    return checks


def tagged_checks(statement: str, values: dict[str, float], unit: float) -> list[Check]:
    """IFRS-tagged totals (OpenDART; DART's own tags by their names without "dart_"): assets equal equity and liabilities, and current plus non-current assets; profit
    is the owners' share plus the non-controlling interests'; the change in cash is closing less opening cash, else
    the cash flows' sections and the exchange effect."""
    def has(*tags: str) -> bool:
        return all(tag in values for tag in tags)

    if statement == "balance":
        if not has("Assets"):
            return [Check("balance", NONE)]
        right = values.get("EquityAndLiabilities")
        if right is None and has("Liabilities", "Equity"):
            right = values["Liabilities"] + values["Equity"]
        if right is None:
            return [Check("balance", NONE)]
        wrong = [] if _close(values["Assets"], right, unit, 2) else [f"Assets {values['Assets']:,.0f} != {right:,.0f}"]
        if has("CurrentAssets", "NoncurrentAssets") and not _close(values["Assets"], values["CurrentAssets"] + values["NoncurrentAssets"], unit, 2):
            wrong.append("Assets != CurrentAssets + NoncurrentAssets")
        return [Check("balance", FAIL if wrong else PASS, "; ".join(wrong))]
    if statement == "income":
        if has("ProfitLoss", "ProfitLossAttributableToOwnersOfParent", "ProfitLossAttributableToNoncontrollingInterests"):
            same = _close(values["ProfitLoss"], values["ProfitLossAttributableToOwnersOfParent"]
                          + values["ProfitLossAttributableToNoncontrollingInterests"], unit, 2)
            return [Check("calc", PASS if same else FAIL, "" if same else "ProfitLoss != owners' + non-controlling")]
        return [Check("calc", NONE)]
    sections = ("CashFlowsFromUsedInOperatingActivities", "CashFlowsFromUsedInInvestingActivities",
                "CashFlowsFromUsedInFinancingActivities")
    change = values.get("IncreaseDecreaseInCashAndCashEquivalents")
    if change is not None and has("CashAndCashEquivalentsAtBeginningOfPeriodCf", "CashAndCashEquivalentsAtEndOfPeriodCf"):
        # Closing less opening cash: KEPCO's Q3 2025 report reconciles its sections with an untagged line (assets
        # moved to held for sale) the sections' sum leaves out.
        moved = values["CashAndCashEquivalentsAtEndOfPeriodCf"] - values["CashAndCashEquivalentsAtBeginningOfPeriodCf"]
        same = _close(change, moved, unit, 2)
        return [Check("cash", PASS if same else FAIL, "" if same else f"change {change:,.0f} != closing - opening {moved:,.0f}")]
    if change is None or not has(*sections):
        return [Check("cash", NONE)]
    total = sum(values[tag] for tag in sections) + values.get("EffectOfExchangeRateChangesOnCashAndCashEquivalents", 0.0)
    same = _close(change, total, unit, 4)
    return [Check("cash", PASS if same else FAIL, "" if same else f"change {change:,.0f} != sections {total:,.0f}")]
