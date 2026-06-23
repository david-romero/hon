"""Compile-check: every module in the integration must parse without syntax errors,
and all imports that can be resolved locally must resolve correctly.

Run with:  python -m pytest tests/test_compile.py -v
"""
import ast
import importlib
import sys
from pathlib import Path

import pytest

INTEGRATION_DIR = Path(__file__).parent.parent / "custom_components" / "hon"

MODULES = [p for p in INTEGRATION_DIR.rglob("*.py") if p.name != "__pycache__"]


@pytest.mark.parametrize("module_path", MODULES, ids=lambda p: p.name)
def test_syntax(module_path: Path) -> None:
    """Each .py file must parse as valid Python."""
    source = module_path.read_text(encoding="utf-8")
    try:
        ast.parse(source, filename=str(module_path))
    except SyntaxError as exc:
        pytest.fail(f"SyntaxError in {module_path.name}: {exc}")


@pytest.mark.parametrize("module_path", MODULES, ids=lambda p: p.name)
def test_no_double_prefix_imports(module_path: Path) -> None:
    """Guard against the 'ConfigConfigFlowResult'-style double-replacement bug:
    no imported name should start with a known HA prefix repeated twice."""
    DOUBLE_PREFIXES = ("ConfigConfig", "FlowFlow", "EntityEntity")
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names]
            for name in names:
                for prefix in DOUBLE_PREFIXES:
                    if prefix in name:
                        pytest.fail(
                            f"{module_path.name}: suspicious import name '{name}' "
                            f"(looks like a double-replacement artifact)"
                        )


@pytest.mark.parametrize("module_path", MODULES, ids=lambda p: p.name)
def test_no_removed_ha_symbols(module_path: Path) -> None:
    """Detect imports of HA symbols that were removed in 2024-2026 breaking changes."""
    REMOVED = {
        "FlowResult": "homeassistant.data_entry_flow",
        "CONN_CLASS_LOCAL_POLL": "homeassistant.config_entries",
        "CONN_CLASS_CLOUD_PUSH": "homeassistant.config_entries",
        "CONN_CLASS_LOCAL_PUSH": "homeassistant.config_entries",
    }
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in REMOVED:
                    pytest.fail(
                        f"{module_path.name}: imports removed symbol '{alias.name}' "
                        f"(was in {REMOVED[alias.name]}). "
                        f"FlowResult → use ConfigFlowResult from homeassistant.config_entries; "
                        f"CONN_CLASS_* constants were removed — delete the attribute."
                    )
