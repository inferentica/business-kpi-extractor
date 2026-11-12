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


def test_words_that_name_brazils_practices_without_making_them_the_basis():
    # Round 69: Petrobras' releases and Ambev's 2013 report read "Brazilian GAAP".
    assert stated_basis("<p>Adjusted EBITDA and Net Debt are not defined under BR GAAP or IFRS.</p><p>The figures are "
                        "prepared in accordance with international accounting standards (IFRS).</p>") == IFRS
    assert stated_basis("<p>The individual financial statements were prepared in accordance with the accounting practices "
                        "adopted in Brazil. In the case of the Company, those practices differ from the IFRS applicable to "
                        "separate financial statements.</p><p>The consolidated statements were prepared in accordance with "
                        "IFRS.</p>") == IFRS
    # Gerdau's 2010 releases.
    assert stated_basis("<p>Prepared in accordance with the international accounting practices issued by the International "
                        "Accounting Standards Board (IASB) and the accounting practices adopted in Brazil.</p>") == IFRS


def test_a_former_basis_is_not_the_documents():
    # Lloyds' 2005 interim report (round 69 read "UK GAAP").
    assert stated_basis("<p>Up to 31 December 2004 the Group prepared its financial statements in accordance with UK "
                        "Generally Accepted Accounting Principles (UK GAAP). On 1 January 2005 the Group implemented "
                        "International Financial Reporting Standards (IFRS). In this document the 2004 comparative financial "
                        "information has been restated, prepared in accordance with IFRS.</p>") == IFRS


def test_a_releases_results_under_a_basis():
    # FEMSA's Q1 2012 release, whose later note on a subsidiary's cash flow names Mexican FRS.
    assert stated_basis("<p>FEMSA announced today its operational and financial results for the first quarter of 2012 under "
                        "International Financial Reporting Standards (IFRS).</p><p>These differences are presented in the "
                        "cash flow statement in accordance with Mexican Financial Reporting Standards.</p>") == IFRS


def test_the_20f_covers_ticked_box_where_no_opinion_is_read():
    # Sabesp's FY2011 20-F (its opinion not in the document's text), Braskem's boxes, Alcon's.
    question = "Indicate by check mark which basis of accounting the registrant has used to prepare the financial statements included in this filing: "
    body = "<p>Our statutory financial statements are prepared in accordance with the accounting practices adopted in Brazil.</p>"
    assert stated_basis("<p>" + question + "U.S. GAAP o International Financial Reporting Standards as issued by the "
                        "International Accounting Standards Board &#254; Other o If &#8220;Other&#8221; has been checked</p>" + body) == IFRS
    assert stated_basis("<p>" + question + "U.S. GAAP &#9744; International Financial Reporting Standards as issued by the "
                        "International Accounting Standards Board &#9746; Other &#9744; If</p>" + body) == IFRS
    assert stated_basis("<p>" + question + "U.S. GAAP X International Financial Reporting Standards as issued Other by the "
                        "International Accounting Standards Board If</p>" + body) == US_GAAP
    assert stated_basis("<p>" + question + "&#9744; U.S. GAAP &#9744; International Financial Reporting Standards &#9746; Other If</p>"
                        + body) == BRAZILIAN_GAAP  # "Other" ticked: the document's words decide


def test_a_brazilian_quarterly_reviews_cvm_standards():
    # Sabesp's Q3 2009 ITR (round 70 took its 20-F's IFRS for it).
    assert stated_basis("<p>Based on our limited review, we are not aware of any material modifications that should be made "
                        "to the accounting information included in the Quarterly Information referred to above in order that "
                        "it be stated in accordance with the standards issued by the Brazilian Securities Commission (CVM) "
                        "applicable to the preparation of Quarterly Information, including CVM Instruction 469/08.</p>") == BRAZILIAN_GAAP
    assert stated_basis("<p>Prepared in accordance with CPC 21 and IAS 34 and the standards issued by the Brazilian Securities "
                        "Commission (CVM).</p>") == IFRS
