"""The package keeps one concern per file: no module grows past MAX_LINES, and each folder imports only the folders
below it, so a reader never reaches into selection or the pipeline, and checks never into a reader."""
import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "statement_extractor"
MAX_LINES = 300
# What each folder may import from this package. A new folder must be added here, with its place in the order.
ALLOWED = {
    "sources": set(),
    "domain": {"sources"},
    "checks": {"domain"},
    "readers": {"domain", "checks", "sources"},
    "selection": {"domain"},
    "store": {"domain"},
    "quality": {"domain", "selection"},
    "pipeline": {"domain", "checks", "readers", "selection", "quality", "sources", "store"},
    # The AI pilot (decision 2026-11-12): it reads tables beside the pipeline; nothing imports it (tests/test_isolation.py).
    "assist": {"domain", "readers", "sources"},
}


def _module(path: Path) -> list[str]:
    return list(path.relative_to(PACKAGE).with_suffix("").parts)


def _targets(path: Path) -> set[tuple[str, ...]]:
    """The package modules a file imports, as paths inside the package."""
    here = _module(path)
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.level:
            base = here[:len(here) - node.level]
            found.add(tuple(base + (node.module.split(".") if node.module else [])))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("statement_extractor."):
            found.add(tuple(node.module.split(".")[1:]))
    return found


def test_no_module_grows_past_the_limit():
    for path in PACKAGE.rglob("*.py"):
        lines = len(path.read_text().splitlines())
        assert lines <= MAX_LINES, f"{path.relative_to(PACKAGE)} has {lines} lines; split it by concern"


def test_every_module_sits_in_a_known_folder():
    for path in PACKAGE.rglob("*.py"):
        parts = _module(path)
        assert len(parts) == 1 and parts[0] in ("__init__", "__main__") or parts[0] in ALLOWED, \
            f"{path.relative_to(PACKAGE)} is outside the known folders"


def test_folders_import_only_the_folders_below_them():
    for path in PACKAGE.rglob("*.py"):
        parts = _module(path)
        if len(parts) == 1:
            continue
        folder = parts[0]
        for target in _targets(path):
            if not target or target[0] == folder:
                continue
            assert target[0] in ALLOWED[folder], f"{'/'.join(parts)} imports {'/'.join(target)}"


def test_each_format_reader_stands_alone():
    """A format's reader (xbrl, html, text) imports no other format; the printed-statement core shared by the HTML and
    plain-text readers imports none of them."""
    for path in (PACKAGE / "readers").rglob("*.py"):
        parts = _module(path)
        if len(parts) < 3:
            continue
        for target in _targets(path):
            if len(target) >= 2 and target[0] == "readers" and target[1] != parts[1]:
                assert target[1] == "printed" and parts[1] != "printed", \
                    f"{'/'.join(parts)} imports another reader: {'/'.join(target)}"
