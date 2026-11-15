"""Property tests for column dating: headers generated in the shapes filings print them (dates in every style met so
far, on one row or split over two, spans per column or shared, weeks and odd month counts, fixed-width text whose cells
run together), each column checked against the period it was built from. Example tests passed while Ross's, Delta's
and Morgan Stanley's headers broke the same rules (2026-11-10)."""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from statement_extractor.readers.html.parse import parse_html
from statement_extractor.readers.printed.dates import column_periods
from statement_extractor.readers.printed.tables import figure_columns
from statement_extractor.readers.text.parse import parse_text

SETTINGS = settings(max_examples=150, deadline=None, derandomize=True, suppress_health_check=[HealthCheck.too_slow, HealthCheck.large_base_example])
MONTHS = list(calendar.month_name)[1:]
WORDS = {3: "Three", 6: "Six", 9: "Nine", 12: "Twelve", 13: "Thirteen", 16: "Sixteen", 24: "Twenty-Four",
         26: "Twenty-Six", 36: "Thirty-Six", 39: "Thirty-Nine", 40: "Forty", 52: "Fifty-Two", 53: "Fifty-Three",
         1: "One", 2: "Two", 4: "Four", 5: "Five", 7: "Seven", 8: "Eight", 10: "Ten", 11: "Eleven"}


def _end(year: int, month: int, day: int | None) -> date:
    return date(year, month, day or calendar.monthrange(year, month)[1])


# A period end: a month end, or any day (52/53-week years).
END = st.builds(_end, st.integers(1995, 2025), st.integers(1, 12), st.one_of(st.none(), st.integers(1, 28)))


def ends(count_min: int = 2, count_max: int = 4):
    """Distinct period ends, newest first as statements print them."""
    return st.lists(END, min_size=count_min, max_size=count_max, unique=True).map(lambda found: sorted(found, reverse=True))


def styled(end: date, style: str) -> tuple[str, str]:
    """(upper row, lower row) a column prints for its date in a style; the lower row may be empty."""
    month = MONTHS[end.month - 1]
    return {
        "us": (f"{month} {end.day}, {end.year}", ""),
        "us_split": (f"{month} {end.day},", str(end.year)),
        "short": (f"{month[:3]} {end.day}, {end.year}", ""),
        "uk": (f"{end.day} {month} {end.year}", ""),
        "uk_short": (f"{end.day} {month[:3]} {end.year}", ""),
        "uk_split": (f"{end.day} {month}", str(end.year)),
        "numeric": (f"{end.month}/{end.day}/{end.year}", ""),
        "dotted_day": (f"{end.day:02d}.{end.month:02d}.{end.year}", ""),
        "dotted_month": (f"{end.month:02d}.{end.day:02d}.{end.year}", ""),
    }[style]


def html_table(title: str, rows: list[list[tuple[str, int]]], columns: int) -> str:
    """A statement table: header rows of (text, colspan) cells after an empty label cell, then four figure rows."""
    head = "".join("<tr><td></td>" + "".join(f'<td colspan="{span}">{text}</td>' for text, span in row) + "</tr>"
                   for row in rows)
    body = "".join(f"<tr><td>{label}</td>" + "".join(f"<td>{10 * (n + 1) + i}</td>" for n in range(columns)) + "</tr>"
                   for i, label in enumerate(("Cash and cash equivalents", "Total assets", "Total liabilities",
                                              "Total equity")))
    return f"<p>{title}</p><table>{head}{body}</table>"


def dated(kind: str, page: str, fiscal_year_end: str = "1231", document_end: date | None = None) -> list:
    periods = column_periods(kind, [parse_html(page)[0]], fiscal_year_end, document_end)
    return [periods[column] for column in sorted(periods)]


STYLES = ["us", "us_split", "short", "uk", "uk_short", "uk_split", "numeric", "dotted_day", "dotted_month"]


@SETTINGS
@given(ends(), st.sampled_from(STYLES))
def test_each_balance_sheet_column_takes_its_own_printed_date(days, style):
    if style.startswith("dotted") and not any(day.day > 12 for day in days):
        return  # "05.04.2001" alone cannot say which is the day; filings print one that can
    cells = [styled(day, style) for day in days]
    rows = [[(upper, 1) for upper, _ in cells]]
    if any(lower for _, lower in cells):
        rows.append([(lower, 1) for _, lower in cells])
    found = dated("balance", html_table("CONSOLIDATED BALANCE SHEETS", rows, len(days)), document_end=days[0])
    assert [period.end for period in found] == days


@SETTINGS
@given(ends())
def test_a_label_cell_naming_each_columns_day_gives_them_in_order(days):
    # Brookfield: "AS AT JUNE 30, AND DECEMBER 31" in the label cell over "2023 | 2022".
    if len({(day.month, day.day) for day in days}) < len(days):
        return
    named = ", AND ".join(f"{MONTHS[day.month - 1].upper()} {day.day}" for day in days)
    years = "".join(f"<td>{day.year}</td>" for day in days)
    body = "".join(f"<tr><td>{label}</td>" + "".join(f"<td>{n}</td>" for n in range(len(days))) + "</tr>"
                   for label in ("Cash", "Total assets", "Total liabilities", "Total equity"))
    page = (f"<p>CONSOLIDATED BALANCE SHEETS</p><table><tr><td>AS AT {named} (MILLIONS)</td>{years}</tr>{body}</table>")
    assert [period.end for period in dated("balance", page, document_end=days[0])] == days


def duration_of(count: int, unit: str) -> str | None:
    days = count * 7 if unit == "Weeks" else {3: 91, 6: 182, 9: 273, 12: 364}.get(count)
    if days is None:
        return None
    return next((name for name, low, high in (("quarter", 80, 122), ("ytd", 165, 210), ("ytd", 245, 295),
                                              ("year", 350, 380)) if low <= days <= high), None)


SPANS = st.one_of(st.tuples(st.sampled_from([3, 6, 9, 12]), st.just("Months")),
                  st.tuples(st.sampled_from([13, 16, 26, 39, 52, 53]), st.just("Weeks")))
ODD = st.tuples(st.sampled_from([1, 2, 4, 5, 7, 8, 10, 11]), st.just("Months"))


@SETTINGS
@given(st.data(), st.booleans())
def test_each_income_column_takes_its_own_span_and_end(data, odd_one):
    # Each column prints its own span: "Three Months Ended June 30, 2023 | Nine Months Ended June 30, 2023", with now
    # and then a column of one, two or four months (Delta's fresh start), which is no statement period.
    days = data.draw(ends())
    spans = [data.draw(SPANS) for _ in days]
    if odd_one:
        spans[data.draw(st.integers(0, len(days) - 1))] = data.draw(ODD)
    style = data.draw(st.sampled_from(["us", "us_split", "uk", "short"]))
    cells = [styled(day, style) for day in days]
    rows = [[(f"{WORDS[count]} {unit} Ended", 1) for count, unit in spans], [(upper, 1) for upper, _ in cells]]
    if any(lower for _, lower in cells):
        rows.append([(lower, 1) for _, lower in cells])
    table = parse_html(html_table("CONSOLIDATED STATEMENTS OF OPERATIONS", rows, len(days)))[0]
    periods = column_periods("income", [table], "1231", days[0])
    columns = sorted(figure_columns(table))
    assert len(columns) == len(days)
    for column, (count, unit), day in zip(columns, spans, days):
        expected = duration_of(count, unit)
        period = periods.get(column)
        if expected is None:
            assert period is None or period.duration is None, (count, unit, day, period)
        else:
            assert period is not None and (period.end, period.duration) == (day, expected), (count, unit, day, period)


@SETTINGS
@given(st.data())
def test_a_span_over_several_columns_dates_each_of_them(data):
    # "Three Months Ended June 30 | Six Months Ended June 30" each over its two years' columns.
    year = data.draw(st.integers(1996, 2025))
    month = data.draw(st.integers(1, 12))
    day = calendar.monthrange(year, month)[1]
    spans = data.draw(st.lists(SPANS, min_size=1, max_size=2, unique=True))
    header = [(f"{WORDS[count]} {unit} Ended {MONTHS[month - 1]} {day},", 2) for count, unit in spans]
    years = [(str(year), 1), (str(year - 1), 1)] * len(spans)
    page = html_table("CONSOLIDATED STATEMENTS OF INCOME", [header, years], 2 * len(spans))
    found = dated("income", page, document_end=date(year, month, day))
    expected = []
    for count, unit in spans:
        for printed in (year, year - 1):
            end = date(printed, month, min(day, calendar.monthrange(printed, month)[1]))
            expected.append((end, duration_of(count, unit)))
    assert [(period.end, period.duration) for period in found] == expected


@SETTINGS
@given(ends(3, 3), st.integers(1, 3))
def test_fixed_width_text_dates_each_column_though_cells_run_together(days, gap):
    # Ross's 1999 10-Q: "May 1, January 30,       May 2," over "1999 1999 1998", the first cell running into the next.
    def right(text: str, end: int, line: list[str]) -> None:
        start = max(0, end - len(text))
        line[start:end] = list(text)

    label = 58
    width = max(len(f"{MONTHS[day.month - 1]} {day.day},") for day in days) + gap  # one space at least, as printed
    stops = [label + (index + 1) * width for index in range(len(days))]
    top, bottom = [" "] * (stops[-1] + 2), [" "] * (stops[-1] + 2)
    for day, stop in zip(days, stops):
        right(f"{MONTHS[day.month - 1]} {day.day},", stop, top)
        right(str(day.year), stop, bottom)
    figures = lambda values: "".join(f"{value:>{width},}" for value in values)
    body = "\n".join(f"  {name:<{label - 2}}{figures([10 * (n + 1) + i for n in range(len(days))])}"
                     for i, name in enumerate(("Cash and cash equivalents", "Total assets", "Total liabilities",
                                               "Total stockholders' equity")))
    text = f"""<TABLE>
COMPANY, INC.
CONDENSED CONSOLIDATED BALANCE SHEETS

($000)
{''.join(top).rstrip()}
{''.join(bottom).rstrip()}
<S>
{body}
</TABLE>"""
    tables = [table for table in parse_text(text) if table.rows]
    periods = column_periods("balance", [tables[0]], "1231", days[0])
    assert [periods[column].end for column in sorted(periods)] == days, (text, periods)
