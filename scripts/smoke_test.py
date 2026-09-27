"""Offline smoke test: every project Python file parses and every src module imports. No network calls.

    python scripts/smoke_test.py
"""
import ast
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".venv", "venv", ".git", "__pycache__", ".pytest_cache", "graphify-out"}

files = [p for p in ROOT.rglob("*.py") if not SKIP.intersection(p.relative_to(ROOT).parts)]
for p in files:
    ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
print(f"Python syntax: OK ({len(files)} files)")

sys.path.insert(0, str(ROOT))
modules = sorted(f"src.{p.stem}" for p in (ROOT / "src").glob("*.py") if p.stem != "__init__")
for name in modules:
    importlib.import_module(name)
print(f"Imports: OK ({', '.join(modules)})")
