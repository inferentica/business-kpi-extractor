from statement_extractor.domain.basis import (BRAZILIAN_GAAP, CHILEAN_GAAP, IFRS, KOREAN_GAAP, MEXICAN_FRS, MEXICAN_GAAP,
                                              UK_GAAP, US_GAAP, stated_basis)

_FAIRLY = "In our opinion, the consolidated financial statements present fairly, in all material respects, the financial position "


def test_a_home_basis_is_the_basis_though_its_opinion_names_us_gaap_after_it():
    # Each 20-F's opinion, as filed: until round 69 every one of them read "US GAAP", the basis it differs from.
    assert stated_basis(_FAIRLY + "in conformity with Mexican Financial Reporting Standards. Mexican Financial Reporting "
                        "Standards vary in certain significant respects from U.S. generally accepted accounting principles.") == MEXICAN_FRS
    assert stated_basis(_FAIRLY + "in conformity with Mexican Financial Reporting Standards, as adopted by Mexico (MFRS).") == MEXICAN_FRS
    assert stated_basis(_FAIRLY + "in conformity with accounting principles generally accepted in Mexico.") == MEXICAN_GAAP
    assert stated_basis(_FAIRLY + "in conformity with accounting principles generally accepted in Chile, which differ in "
                        "certain respects from accounting principles generally accepted in the United States of America.") == CHILEAN_GAAP
    assert stated_basis(_FAIRLY + "in conformity with accounting practices adopted in Brazil. Accounting practices adopted in "
                        "Brazil vary in certain significant respects from accounting principles generally accepted in the United States.") == BRAZILIAN_GAAP
    assert stated_basis(_FAIRLY + "in conformity with generally accepted accounting principles in Brazil.") == BRAZILIAN_GAAP
    assert stated_basis(_FAIRLY + "in accordance with the Korea Electric Power Corporation Act, Accounting Regulations for Public "
                        "Enterprise and accounting principles generally accepted in the Republic of Korea.") == KOREAN_GAAP
    assert stated_basis(_FAIRLY + "in conformity with the Financial Accounting Standards as established by the Financial "
                        "Supervisory Commission of the Republic of Korea (see Note 2).") == KOREAN_GAAP
    assert stated_basis(_FAIRLY + "in conformity with accounting principles generally accepted in the United Kingdom. Accounting "
                        "principles generally accepted in the United Kingdom vary in certain significant respects.") == UK_GAAP


def test_brazilian_practices_and_ifrs_together_are_ifrs():
    assert stated_basis(_FAIRLY + "in accordance with accounting practices adopted in Brazil and with International Financial "
                        "Reporting Standards (IFRS) issued by the IASB.") == IFRS


def test_korean_ifrs_is_ifrs():
    assert stated_basis(_FAIRLY + "in accordance with Korean International Financial Reporting Standards.") == IFRS


def test_us_gaap_in_its_other_wording():
    # CEMIG's 2009 20-F: right only by chance until round 69, through a later "U.S. GAAP".
    assert stated_basis(_FAIRLY + "in conformity with U.S. generally accepted accounting principles. We also have audited, in "
                        "accordance with the standards of the Public Company Accounting Oversight Board.") == US_GAAP


def test_an_opinion_without_its_with_and_the_cover_pages_question():
    # Braskem's 2022 20-F: the opinion lacks "with", and the cover's question to emerging growth companies named U.S. GAAP.
    cover = ("<p>If an emerging growth company that prepares its financial statements in accordance with U.S. GAAP, indicate "
             "by check mark if the registrant has elected not to use the extended transition period.</p>")
    assert stated_basis(cover + _FAIRLY + "in conformity International Financial Reporting Standards (IFRS) as issued by the "
                        "International Accounting Standards Board (IASB).") == IFRS
    assert stated_basis(cover) is None
    # CEMEX's 2016 opinion: "International Reporting Standards".
    assert stated_basis(_FAIRLY + "in conformity with International Reporting Standards as issued by the International "
                        "Accounting Standards Board.") == IFRS
