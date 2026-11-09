from statement_extractor.checks.subtotals import subtotals


def test_an_unlabelled_subtotal_of_three_or_more_lines_sums_exactly():
    # Lloyds: "Financial assets at amortised cost" after its four lines, no "Total" in its label.
    lloyds = [("Loans and advances to banks", 0, 11333.0), ("Loans and advances to customers", 0, 450720.0),
              ("Reverse repurchase agreements", 0, 36006.0), ("Debt securities", 0, 12849.0),
              ("Financial assets at amortised cost", 0, 510908.0)]
    assert subtotals(lloyds) == {4: 0}
    # Apple's 1999 investing lines: "Other" 32 is no sum of 45 - 12 + 0 = 33, rounding allowance or not.
    apple = [("Net proceeds from property, plant, and equipment retirements", 0, 45.0),
             ("Purchase of property, plant, and equipment", 0, -12.0), ("Proceeds from sale of ARM shares", 0, 0.0),
             ("Other", 0, 32.0)]
    assert 3 not in subtotals(apple)
