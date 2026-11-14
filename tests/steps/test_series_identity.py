"""steps.series_identity, step by step, on stored figures alone."""
from types import SimpleNamespace

from kpi_extractor.pipeline import SymbolPipeline


class Control:
    def call(self, operation, **payload):
        return {}


def run(values, names=None):
    pipeline = SymbolPipeline(Control(), "AAPL", quarters=4, force=False, log=lambda *_: None)
    pipeline.profile = SimpleNamespace(name="Apple", fiscal_year_end="0930", foreign=False)
    pipeline.values = {(v["group_key"], v["kpi_key"], v["fiscal_year"], v["fiscal_period"]): v for v in values}
    pipeline.names = names or {}
    return pipeline


def value(group, kpi, label, end, group_label="Revenue by product", year="2024", period="Q1"):
    return {"group_key": group, "kpi_key": kpi, "kpi_label": label, "group_label": group_label, "period_end": end,
            "fiscal_year": year, "fiscal_period": period, "method": "xbrl", "validation_status": "verified", "value": 1.0}


def test_an_xbrl_series_takes_one_name_across_periods_its_latest():
    old = value("products", "iphone", "iPhone (a)", "2023-12-30", group_label="Net sales by category", year="2023")
    new = value("products", "iphone", "iPhone", "2024-12-28")
    renamed = run([old, new])._harmonize_labels()
    assert [(v["fiscal_year"], v["group_label"], v["kpi_label"]) for v in renamed] == [("2023", "Revenue by product", "iPhone")]


def test_the_owners_name_wins_and_ai_groups_keep_their_own():
    xbrl = value("products", "iphone", "iPhone", "2024-12-28")
    ai = {**value("kpi_products", "iphone", "iPhone units", "2024-12-28"), "method": "ai"}
    renamed = run([xbrl, ai], names={"products": {"label": "Products", "members": {"iphone": "iPhone sales"}}})._harmonize_labels()
    assert [(v["group_key"], v["group_label"], v["kpi_label"]) for v in renamed] == [("products", "Products", "iPhone sales")]
