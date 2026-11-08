"""p2-tieout: a breakdown row held only for its group stands when its own row gives last year's figure."""

from kpi_extractor.steps.series_checks import TIES_OUT, tied_rows


def row(kpi, value, end, status="needs_review", notes=("parts differ from the total by 3.8%",), numbers=None, row_at=1,
        filing="A2", table="T1", year="2026", period="Q2"):
    return {"group_key": "kpi_fees", "kpi_key": kpi, "kpi_label": kpi, "group_kind": "revenue_breakdown", "method": "ai",
            "validation_status": status, "notes": list(notes), "value": value, "period_end": end, "fiscal_year": year,
            "fiscal_period": period, "source_accession": filing,
            "locator": {"table": table, "row": row_at, "col": 1, "scale": 1000.0, "row_numbers": numbers or []}}


def test_a_row_that_gives_last_years_accepted_figure_stands():
    # First Financial's bankcard income: 3,032 now, 3,737 a year ago as the row prints it and as last year's release gave.
    last = row("bankcard", 3_737_000.0, "2025-06-30", status="verified", notes=(), filing="A1", year="2025")
    now = row("bankcard", 3_032_000.0, "2026-06-30", numbers=[3032.0, 3737.0, -18.9, 6612.0])
    [stood] = tied_rows([last, now])
    assert stood["validation_status"] == "verified" and TIES_OUT in stood["notes"]


def test_last_years_figure_held_only_for_its_group_counts_when_another_filing_read_it():
    last = row("cards", 737_000_000.0, "2025-09-30", notes=("total matches no figure the company reported in XBRL",),
               filing="A1", year="2025", period="Q3")
    now = row("cards", 769_000_000.0, "2026-09-30", notes=("total matches no figure the company reported in XBRL",),
              numbers=[769.0, 772.0, 737.0, 4.0], year="2026", period="Q3")
    now["locator"]["scale"] = 1e6
    assert [v["kpi_key"] for v in tied_rows([last, now])] == ["cards"]
    # The same filing read twice is no second filing.
    assert tied_rows([{**last, "source_accession": "A2"}, now]) == []


def test_a_row_held_for_itself_or_shared_or_counted_twice_keeps_its_hold():
    last = row("fx", 13_760_000.0, "2025-06-30", status="verified", notes=(), filing="A1", year="2025")
    scaled = row("fx", 13_101_000.0, "2026-06-30", notes=("amount far from its other periods or from revenue: a scale not stated",),
                 numbers=[13101.0, 13760.0])
    assert tied_rows([last, scaled]) == []
    # Axia's "Contractual" and "Contractual Revenue - Transmission": two KPIs on one row.
    twin = row("fx_twin", 13_101_000.0, "2026-06-30", numbers=[13101.0, 13760.0])
    plain = row("fx", 13_101_000.0, "2026-06-30", numbers=[13101.0, 13760.0])
    assert tied_rows([last, plain, twin]) == []
    # PNC: "Other noninterest income" (214) is Visa gains (11), securities (-1) and "Other" (204) together.
    lines = [row(k, v * 1e6, "2026-09-30", row_at=r, numbers=[v, v]) for k, v, r in (("visa", 11.0, 20), ("secs", -1.0, 21), ("other", 204.0, 22))]
    subtotal = row("other_total", 214e6, "2026-09-30", table="T5", row_at=9, numbers=[214.0, 214.0])
    for item in (*lines, subtotal):
        item["locator"]["scale"] = 1e6
    years = [row(v["kpi_key"], v["value"], "2025-09-30", status="verified", notes=(), filing="A1", year="2025", row_at=v["locator"]["row"],
                 table=v["locator"]["table"]) for v in (*lines, subtotal)]
    assert tied_rows([*years, *lines, subtotal]) == []


def test_a_row_the_next_years_release_confirmed_stands():
    now = row("wealth", 4_757_000.0, "2025-09-30", notes=("parts differ from the total by 36.4%", "confirmed by the next year's release"))
    assert [v["kpi_key"] for v in tied_rows([now])] == ["wealth"]
