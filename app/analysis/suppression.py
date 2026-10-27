"""Small cell suppression.

Every aggregate cell built from fewer households than the configured threshold is
withheld: no value, no count, only a marker the clients translate into
"fewer than 10". The threshold comes from application settings so an administrator
can change it without re-running the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SUPPRESSED_TRANSLATION_KEY = "analysis.fewer_than_threshold"


@dataclass(frozen=True)
class Suppressed:
    """One aggregate value plus the suppression decision."""

    value: float | int | None
    suppressed: bool
    count: int | None
    threshold: int

    @property
    def translation_key(self) -> str | None:
        return SUPPRESSED_TRANSLATION_KEY if self.suppressed else None

    def as_dict(self) -> dict[str, Any]:
        if self.suppressed:
            return {
                "value": None,
                "count": None,
                "suppressed": True,
                "translation_key": SUPPRESSED_TRANSLATION_KEY,
                "threshold": self.threshold,
            }
        return {
            "value": _round(self.value),
            "count": self.count,
            "suppressed": False,
            "translation_key": None,
            "threshold": self.threshold,
        }


def _round(value: float | int | None) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return round(float(value), 4)


def cell(value: float | int | None, count: int | None, threshold: int) -> Suppressed:
    """Suppress when the household count behind the cell is below the threshold."""
    if count is None:
        return Suppressed(None, True, None, threshold)
    if count < threshold:
        return Suppressed(None, True, None, threshold)
    return Suppressed(value, False, count, threshold)


def share(numerator: int, denominator: int) -> float | None:
    if not denominator:
        return None
    return numerator / denominator


def weighted_share(numerator: float | None, denominator: float | None) -> float | None:
    if not denominator:
        return None
    return float(numerator or 0) / float(denominator)
