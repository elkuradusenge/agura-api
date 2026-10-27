"""Chunked dta reading must cover every row exactly once."""

from __future__ import annotations

import pyreadstat

from app.analysis.reader import count_dta_rows, iter_dta_chunks


def test_chunks_cover_every_row_once(synthetic_dta):
    full, meta = pyreadstat.read_dta(synthetic_dta)
    total = count_dta_rows(synthetic_dta)
    assert total == len(full)

    seen = 0
    for frame, _ in iter_dta_chunks(synthetic_dta, 250):
        assert len(frame) <= 250
        assert list(frame.columns) == list(full.columns)
        seen += len(frame)
    assert seen == len(full)


def test_chunk_size_larger_than_the_file_yields_one_chunk(synthetic_dta):
    chunks = list(iter_dta_chunks(synthetic_dta, 1_000_000))
    assert len(chunks) == 1


def test_encoding_argument_is_accepted(synthetic_dta):
    chunks = list(iter_dta_chunks(synthetic_dta, 500, encoding="cp1252"))
    assert sum(len(frame) for frame, _ in chunks) > 0


def test_invalid_chunk_size_is_rejected(synthetic_dta):
    import pytest

    with pytest.raises(ValueError):
        list(iter_dta_chunks(synthetic_dta, 0))
