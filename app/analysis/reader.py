"""Chunked reading of Stata files.

pyreadstat.read_file_in_chunks does not forward the encoding argument, and the
census file is cp1252, so the chunks are read with explicit row offsets instead.
"""

from __future__ import annotations

from collections.abc import Iterator

import pandas as pd
import pyreadstat


def iter_dta_chunks(
    path: str, chunk_size: int, encoding: str | None = None
) -> Iterator[tuple[pd.DataFrame, object]]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    options: dict[str, object] = {"apply_value_formats": False}
    if encoding:
        options["encoding"] = encoding

    offset = 0
    while True:
        frame, meta = pyreadstat.read_dta(path, row_limit=chunk_size, row_offset=offset, **options)
        if frame.empty:
            return
        yield frame, meta
        if len(frame) < chunk_size:
            return
        offset += len(frame)


def count_dta_rows(path: str, encoding: str | None = None) -> int | None:
    options: dict[str, object] = {"metadataonly": True}
    if encoding:
        options["encoding"] = encoding
    _, meta = pyreadstat.read_dta(path, **options)
    return getattr(meta, "number_rows", None)
