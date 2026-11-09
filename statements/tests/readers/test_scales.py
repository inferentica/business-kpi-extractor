from statement_extractor.readers.printed.scales import line_scale, share_scale, statement_scale
from statement_extractor.readers.printed.tables import Table


def table(context):
    return [Table(rows=[], header=[], context=context, index=0)]


def test_money_and_share_scales_are_read_apart():
    apple = table("CONSOLIDATED STATEMENTS OF OPERATIONS (In millions, except share amounts which are reflected in "
                  "thousands and per share amounts)")
    assert statement_scale(apple) == 1e6 and share_scale(apple, 1e6) == 1e3
    coke = table("(In millions except per share data)")
    assert statement_scale(coke) == 1e6 and share_scale(coke, 1e6) == 1e6
    units = table("(in thousands, except share and per share data)")
    assert statement_scale(units) == 1e3 and share_scale(units, 1e3) == 1.0
    intel = table("(In millions, except per share amounts; shares in thousands)")
    assert statement_scale(intel) == 1e6 and share_scale(intel, 1e6) == 1e3


def test_a_share_line_can_name_its_own_scale():
    assert line_scale("BASIC WEIGHTED AVERAGE SHARES OUTSTANDING (THOUSANDS)") == 1e3
    assert line_scale("", "Shares used in computing earnings per share (in thousands):") == 1e3
    assert line_scale("Average shares outstanding") is None
    assert line_scale("Shares Outstanding ('K)") == 1e3  # TSMC's 1999-2000 releases


def test_thousands_written_as_zeros():
    from statement_extractor.readers.printed.tables import Table
    for caption in ("($000, except stores and per share data, unaudited)", "(In $000s, except unit amounts)",
                    "(In 000s, except per unit amounts)", "($000's)", "(000 omitted)"):
        assert statement_scale([Table(rows=[], header=[], context="Consolidated Balance Sheets " + caption, index=0)]) == 1e3
    assert statement_scale([Table(rows=[], header=[], context="Revenue grew $1,000 in total (in millions)", index=0)]) == 1e6
