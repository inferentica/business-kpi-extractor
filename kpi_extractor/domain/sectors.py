"""Industry hints for proposing a company's KPI list, chosen by its SEC SIC code.

Hints are examples of what a company in the industry usually reports, never a list to fill: the reader still tracks only
what the company itself gives as a number for the quarter, and anything else that drives its business. A hint can also
lift a general "never track" rule that would block the industry's own measures (a bank's net interest margin, loans and
deposits). The SIC code is coarse (ConocoPhillips files as "Petroleum Refining"), so each hint stays broad.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Sector:
    key: str
    name: str
    examples: str
    # Measures the general "Never track" list would block that this industry reports as its own KPIs.
    allowed: str = ""


SECTORS = {
    "bank": Sector("bank", "Banks",
                   "net interest margin, average or ending loans and deposits (totals and by type), loan-to-deposit "
                   "ratio, net charge-off ratio, nonperforming assets, allowance coverage, CET1 and other capital "
                   "ratios, efficiency ratio, assets under management or administration, wealth or trust assets, "
                   "branch and ATM counts, employees, noninterest (fee) income by type",
                   "net interest margin, net charge-off and nonperforming ratios, efficiency ratio, capital ratios "
                   "(CET1, Tier 1, leverage), loan, deposit and client asset balances, and fee income by type (a "
                   "breakdown of noninterest income whose parts add up to its total)"),
    "insurance": Sector("insurance", "Insurers",
                        "premiums written or earned by line, combined or loss ratio, medical cost ratio, policies or "
                        "members, account values, assets under management, sales or flows by product, persistency, "
                        "advisors or agents",
                        "combined, loss, expense and medical cost ratios, account values and assets under management, "
                        "and premiums or sales by line"),
    "asset_manager": Sector("asset_manager", "Asset managers and brokers",
                            "assets under management or supervision (by type or channel), fee-paying AUM, net inflows "
                            "or outflows, capital raised or deployed, dry powder, fee-related earnings, management fee "
                            "rates, client assets, client accounts, advisors",
                            "assets under management or supervision, net flows, capital raised and deployed, "
                            "fee-related earnings and fee rates"),
    "reit": Sector("reit", "Real estate",
                   "occupancy, same-property or same-store NOI growth, leasing volume and spreads, rent per unit or "
                   "square foot, properties, sites or square feet owned, annualized base rent and its shares by tenant "
                   "industry, credit rating or region (as a mix), development pipeline, "
                   "bookings, megawatts or kilowatts leased",
                   "same-property NOI and its growth, occupancy, annualized rent and leasing spreads"),
    "energy": Sector("energy", "Energy",
                     "production volumes by product or region (oil, gas, NGLs, BOE per day), realized prices, reserves, "
                     "refinery throughput and utilization, crack spreads or margins per barrel, rigs, fleet, dayrates, "
                     "utilization, contract backlog, volumes sold",
                     "realized prices and refining margins per barrel or per unit"),
    "utility": Sector("utility", "Utilities",
                      "energy generated or sold (by source), installed capacity, customers, rate base, load or demand "
                      "growth, average prices or tariffs, transmission and distribution volumes",
                      "average prices or tariffs per unit and rate base"),
    "software": Sector("software", "Software and internet",
                       "ARR, net revenue retention, customers (total and above a spend level), remaining performance "
                       "obligations, billings, subscribers, users (DAU, MAU), ARPU, cloud or subscription revenue, "
                       "bookings, transactions, gross merchandise or payment volume"),
    "semis_hardware": Sector("semis_hardware", "Semiconductors and hardware",
                             "revenue by product, platform, end market or technology node, wafer or unit shipments, "
                             "average selling price, orders and book-to-bill, backlog, capacity utilization"),
    "aerospace_defense": Sector("aerospace_defense", "Aerospace and defense",
                                "backlog (total, commercial, defense), orders or awards, book-to-bill, deliveries by "
                                "program or aircraft, shop visits, flight hours"),
    "industrial": Sector("industrial", "Industrials, construction and services",
                         "backlog (total, fixed, under master service agreements), orders and book-to-bill, units "
                         "delivered or booked, organic growth by segment, customers or locations served"),
    "transport": Sector("transport", "Transport and travel",
                        "volumes (packages, shipments, room nights, passengers, trips), yield or revenue per unit, "
                        "gross bookings, average daily volume, load factor, fleet"),
    "auto": Sector("auto", "Autos and parts",
                   "deliveries and production by model, vehicles in operation, order backlog, units booked, new "
                   "business awards, average selling price, subscriptions"),
    "consumer": Sector("consumer", "Consumer products and retail",
                       "comparable or same-store sales, store count, openings and closings, selling square footage, "
                       "e-commerce share, units sold, active customers, transactions, "
                       "average ticket"),
    "healthcare": Sector("healthcare", "Healthcare",
                         "revenue by product, franchise, segment or region, patients, members or plans, procedures or "
                         "visits, bed or capacity utilization, revenue per member, prescriptions, pipeline milestones",
                         "medical cost and loss ratios, and capacity utilization"),
    "materials": Sector("materials", "Materials and mining",
                        "production and sales volumes by product, realized prices, capacity utilization, shipments",
                        "realized prices per unit"),
    "telecom_media": Sector("telecom_media", "Telecom and media",
                            "subscribers or customers (mobile, broadband, TV) and their net additions, churn, ARPU, "
                            "streaming hours, households reached, markets or stations, advertising and "
                            "retransmission revenue",
                            "churn rates and ARPU"),
    "education": Sector("education", "Education",
                        "new student starts, total or average enrollment, completion or placement rates, campuses, "
                        "revenue per student"),
}

# SIC code ranges (inclusive), most specific first. 7389 "Business Services, NEC" (Visa, Mastercard) is too broad to
# name an industry, so it gets no hint.
_SIC_RANGES: tuple[tuple[int, int, str], ...] = (
    (6020, 6036, "bank"), (6110, 6111, "bank"),
    (6300, 6411, "insurance"),
    (6798, 6798, "reit"), (6500, 6553, "reit"),
    (6199, 6199, "asset_manager"), (6200, 6289, "asset_manager"), (6720, 6799, "asset_manager"),
    (1300, 1399, "energy"), (2900, 2999, "energy"), (4610, 4619, "energy"), (4922, 4925, "energy"),
    (5171, 5172, "energy"), (1220, 1241, "energy"),
    (4900, 4991, "utility"),
    (7370, 7379, "software"),
    (3720, 3729, "aerospace_defense"), (3760, 3769, "aerospace_defense"), (3812, 3812, "aerospace_defense"),
    (3711, 3716, "auto"),
    (3570, 3579, "semis_hardware"), (3661, 3679, "semis_hardware"), (3820, 3829, "semis_hardware"),
    (3550, 3559, "semis_hardware"),
    (2830, 2836, "healthcare"), (3840, 3851, "healthcare"), (5047, 5047, "healthcare"), (5122, 5122, "healthcare"),
    (8000, 8099, "healthcare"),
    (4810, 4899, "telecom_media"), (7810, 7819, "telecom_media"), (2710, 2741, "telecom_media"),
    (4000, 4799, "transport"),
    (8200, 8299, "education"),
    (1000, 1499, "materials"), (2800, 2829, "materials"), (2840, 2899, "consumer"), (2600, 2699, "materials"),
    (3300, 3399, "materials"),
    (2000, 2399, "consumer"), (2500, 2599, "consumer"), (3140, 3199, "consumer"), (3940, 3949, "consumer"),
    (5200, 5999, "consumer"),
    (1500, 1799, "industrial"), (3400, 3549, "industrial"), (3560, 3569, "industrial"), (3580, 3660, "industrial"),
    (3680, 3699, "industrial"), (3700, 3710, "industrial"), (3717, 3719, "industrial"), (3730, 3759, "industrial"),
    (3770, 3811, "industrial"), (3813, 3819, "industrial"), (3830, 3839, "industrial"), (7300, 7369, "industrial"),
    (8700, 8748, "industrial"),
)


def sector_of(sic: int | str | None) -> Sector | None:
    """The industry hint for a SIC code, or None when the code is missing or matches no industry here."""
    try:
        code = int(str(sic).strip())
    except (TypeError, ValueError):
        return None
    return next((SECTORS[key] for low, high, key in _SIC_RANGES if low <= code <= high), None)


def sector_hint(sector: Sector | None) -> str:
    """The lines Propose is given for a company's industry: examples to look for, never a list to fill."""
    if sector is None:
        return ""
    lines = [f"Industry hints ({sector.name}): companies in this industry often report {sector.examples}. These are "
             "examples of what to look for, not a list to fill, and they add to the Track list above, never replace it: "
             "still track every revenue breakdown and operating metric it asks for (a quarterly breakdown too when its "
             "XBRL version is annual). Track only what this company itself gives as a number for the quarter, never "
             "invent or rename a KPI to match a hint, and also track any other KPI that drives this company's business "
             "even if it is not named here."]
    if sector.allowed:
        lines.append(f"For this industry, track these despite the general \"Never track\" list: {sector.allowed}.")
    return "\n".join(lines)
