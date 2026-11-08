"""The statements package stays apart from the KPI extractor: it reaches the shared base layer only through base.py,
and nothing in it can call an AI."""
import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "statement_extractor"
# The KPI extractor's base layer this package may use, and only from base.py.
SHARED = {"kpi_extractor.sec", "kpi_extractor.fiscal"}
# AI clients and the KPI extractor's AI, control plane, readers and pipeline: never imported here.
FORBIDDEN = ("openai", "anthropic", "dashscope", "deepseek", "kpi_extractor.ai", "kpi_extractor.control",
             "kpi_extractor.pipeline", "kpi_extractor.steps", "kpi_extractor.store")


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_only_base_reaches_the_kpi_extractor():
    for path in PACKAGE.rglob("*.py"):
        reached = {name for name in _imports(path) if name.split(".")[0] == "kpi_extractor"}
        if path.name == "base.py":
            assert reached <= SHARED, f"base.py reaches {reached - SHARED}"
        else:
            assert not reached, f"{path.name} imports {reached}; go through base.py"


def test_nothing_can_call_an_ai():
    for path in PACKAGE.rglob("*.py"):
        for name in _imports(path):
            assert not name.startswith(FORBIDDEN), f"{path.name} imports {name}"
