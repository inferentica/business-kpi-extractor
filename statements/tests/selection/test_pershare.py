"""Per-share figures tagged at the statement's money scale (selection/pershare.py)."""
from datetime import date

from statement_extractor.domain.model import Check, Reading
from statement_extractor.selection.pershare import printed_per_share, scale_ties, scaled


def reading(accession, filed, eps, source="xbrl", label="Basic and diluted earnings per share", shares=None, year="2019",
            unit_kind="per_share", basis="IFRS", decimals=None):
    concept = "pac_BasicAndDilutedEarningsPerShare" if source == "xbrl" else "printed_basic_and_diluted"
    lines = [{"line": 0, "concept": "ifrs-full_ProfitLoss", "label": "Net income", "level": 0, "is_heading": False,
              "value": 5_600_000_000.0, "unit_kind": "money"},
             {"line": 1, "concept": concept, "label": label, "level": 0, "is_heading": False, "value": eps,
              "unit_kind": unit_kind, "decimals": decimals}]
    if shares:
        lines.append({"line": 2, "concept": "ifrs-full_WeightedAverageShares", "label": "Weighted average shares",
                      "level": 0, "is_heading": False, "value": shares, "unit_kind": "shares"})
    return Reading(statement="income", duration="year", fiscal_year=year, fiscal_period="FY",
                   period_end=date(int(year), 12, 31), period_start=date(int(year), 1, 1), currency="MXN",
                   accession=accession, form="20-F", filed=filed, own=False, source=source, basis=basis,
                   checks=[Check("calc", "pass")], lines=lines)


def test_a_per_share_figure_tagged_at_the_money_scale_takes_its_filings_printed_figure():
    # PAC's 2022 20-F tags its 2019 earnings per share, printed 10.1986 pesos, at scale 3: 10,198.6.
    tagged = reading("pac-2022", date(2022, 4, 22), 10_198.6, decimals=1)
    printed = reading("pac-2022", date(2022, 4, 22), 10.1986, source="html", decimals=4)
    older = reading("pac-2021", date(2021, 4, 22), 10.1986, source="html", decimals=4)
    assert scale_ties([tagged, older]) == {"pac-2022"}
    assert printed_per_share([tagged, printed]) == 1
    eps = tagged.lines[1]
    assert (eps["value"], eps["decimals"]) == (10.1986, 4)
    assert tagged.checks[-1].name == "per_share" and "tagged 10198.6" in tagged.checks[-1].detail
    assert tagged.status == "kept"


def test_a_printed_label_of_its_own_is_matched_by_the_one_figure_it_can_be():
    # Picpay's 2025 20-F prints 5,457 reais a share and tags it at scale 3; its release labels the line its own way.
    tagged = reading("pics-20f", date(2026, 4, 30), 5_457_000.0, label="Earnings per share – basic", decimals=-3)
    printed = reading("pics-20f", date(2026, 4, 30), 5_457.0, source="html",
                      label="Earnings per share – basic and diluted", decimals=0)
    assert printed_per_share([tagged, printed]) == 1
    assert tagged.lines[1]["value"] == 5_457.0


def test_the_shown_figures_rounding_decides():
    assert scaled(1_241.6, 1.24, 2) == 3  # Picpay's first quarter of 2026: 1.2416 tagged at scale 3, 1.24 printed
    assert scaled(9_584.4, 9.5844, 4) == 3
    assert scaled(9_584.4, 9.59, 2) == 0
    assert scaled(2.08, 2_080_000.0, -4) == 0  # a tag below the print is never taken from it
    assert scaled(-3_746.1, 3.7461, 4) == 0


def test_a_split_is_no_scale_tie():
    # A 1-for-1,000 reverse split: earnings a share a thousand times larger, shares a thousand times fewer.
    before = reading("a2024", date(2024, 4, 1), 0.0102, shares=530_000_000_000.0)
    after = reading("a2025", date(2025, 4, 1), 10.2, shares=530_000_000.0)
    assert scale_ties([before, after]) == set()
    assert scale_ties([before, reading("a2025", date(2025, 4, 1), 10.2, shares=530_000_000_000.0)]) == {"a2025"}


def test_a_tag_below_its_print_is_left_as_tagged():
    # Lloyds tags 0.029 pounds a share where its statement prints 2.9 pence; a printed reader that took a table's
    # millions for a per-share row reads 2,080,000 where the tag is 2.08.
    pence = reading("lyg", date(2018, 8, 3), 0.029)
    printed = reading("lyg", date(2018, 8, 3), 2.9, source="html", decimals=1)
    misread = reading("lyg", date(2018, 8, 3), 2_080_000.0, source="html", unit_kind="money")
    assert scale_ties([pence, printed, misread]) == set()
    assert printed_per_share([pence, printed]) == 0 and pence.lines[1]["value"] == 0.029


def test_the_printed_statement_must_be_on_the_same_basis():
    tagged = reading("pac-2022", date(2022, 4, 22), 10_198.6)
    printed = reading("pac-2022", date(2022, 4, 22), 10.1986, source="html", basis="Mexican FRS", decimals=4)
    assert printed_per_share([tagged, printed]) == 0 and tagged.lines[1]["value"] == 10_198.6


def test_a_few_cents_are_never_another_figures_rounding():
    assert scaled(0.88, 0.006, 2) == 0  # 0.00088 rounds to 0.00, not to 0.01
    assert scaled(12.0, 0.01, 2) == 0
