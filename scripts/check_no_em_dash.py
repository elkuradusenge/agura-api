"""Fail when the em dash character appears anywhere in the source tree.

Run directly (python scripts/check_no_em_dash.py) or through the test suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

EM_DASH = "\u2014"

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "uploads",
    "storage",
    "dist",
    "build",
}

TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".toml",
    ".ini",
    ".cfg",
    ".yaml",
    ".yml",
    ".sql",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".mako",
    ".example",
    "",
}


def iter_text_files(root: Path):
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix not in TEXT_SUFFIXES:
            continue
        yield path


def find_em_dashes(root: Path) -> list[tuple[Path, int, int]]:
    hits: list[tuple[Path, int, int]] = []
    for path in iter_text_files(root):
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if EM_DASH not in content:
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            column = line.find(EM_DASH)
            if column >= 0:
                hits.append((path, line_number, column + 1))
    return hits


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    root = Path(args[0]).resolve() if args else Path(__file__).resolve().parent.parent
    hits = find_em_dashes(root)
    if not hits:
        print("No em dash found")
        return 0
    for path, line, column in hits:
        print(f"{path}:{line}:{column}: em dash is not allowed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
