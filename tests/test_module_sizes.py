"""Each module holds one part of the extractor's logic: none grows past 600 lines."""

from pathlib import Path

LIMIT = 600


def test_no_module_grows_past_its_limit():
    root = Path(__file__).resolve().parents[1] / "kpi_extractor"
    too_long = []
    for path in sorted(root.rglob("*.py")):
        name = path.relative_to(root).as_posix()
        lines = len(path.read_text().splitlines())
        if lines > LIMIT:
            too_long.append(f"{name}: {lines} lines (limit {LIMIT})")
    assert not too_long, "split these modules: " + "; ".join(too_long)
