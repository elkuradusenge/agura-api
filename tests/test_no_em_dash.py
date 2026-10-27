"""The em dash character must never appear in either repository."""

from __future__ import annotations

from pathlib import Path

from scripts.check_no_em_dash import find_em_dashes

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_source_tree_has_no_em_dash():
    hits = find_em_dashes(PROJECT_ROOT)
    assert hits == [], "\n".join(f"{path}:{line}:{column}" for path, line, column in hits)
