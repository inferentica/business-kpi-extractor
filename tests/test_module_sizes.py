"""Each module holds one part of the extractor's logic: none grows past 600 lines. The few that were already larger when
the rule began may only shrink, so splitting them stays the way to add to them."""

from pathlib import Path

LIMIT = 600
# Modules over the limit on 2026-11-08, capped at their size then.
SHRINK_ONLY = {"documents/document.py": 630, "steps/ai_reading.py": 622, "steps/series_checks.py": 617}


def test_no_module_grows_past_its_limit():
    root = Path(__file__).resolve().parents[1] / "kpi_extractor"
    too_long = []
    for path in sorted(root.rglob("*.py")):
        name = path.relative_to(root).as_posix()
        lines = len(path.read_text().splitlines())
        if lines > SHRINK_ONLY.get(name, LIMIT):
            too_long.append(f"{name}: {lines} lines (limit {SHRINK_ONLY.get(name, LIMIT)})")
    assert not too_long, "split these modules: " + "; ".join(too_long)
