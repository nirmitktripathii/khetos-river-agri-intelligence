import importlib
from pathlib import Path

import pytest

MODULES = sorted(f"src.{p.stem}" for p in (Path(__file__).resolve().parents[1] / "src").glob("*.py")
                 if p.stem != "__init__")


@pytest.mark.parametrize("name", MODULES)
def test_imports(name):
    importlib.import_module(name)
