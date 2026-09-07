from __future__ import annotations

from datetime import datetime


def intervals_overlap(
    first_from: datetime | None,
    first_to: datetime | None,
    second_from: datetime | None,
    second_to: datetime | None,
) -> bool:
    """Return whether half-open intervals [from, to) overlap."""
    if first_to is not None and second_from is not None:
        if first_to <= second_from:
            return False
    if second_to is not None and first_from is not None:
        if second_to <= first_from:
            return False
    return True


def interval_contains(
    valid_from: datetime | None,
    valid_to: datetime | None,
    moment: datetime,
) -> bool:
    """Return whether moment belongs to half-open [from, to)."""
    if valid_from is not None and moment < valid_from:
        return False
    if valid_to is not None and moment >= valid_to:
        return False
    return True


def overlap_bounds(
    first_from: datetime | None,
    first_to: datetime | None,
    second_from: datetime | None,
    second_to: datetime | None,
) -> dict[str, str | None]:
    starts = [value for value in (first_from, second_from) if value]
    ends = [value for value in (first_to, second_to) if value]
    overlap_from = max(starts) if starts else None
    overlap_to = min(ends) if ends else None
    return {
        "overlap_from": overlap_from.isoformat() if overlap_from else None,
        "overlap_to": overlap_to.isoformat() if overlap_to else None,
        "interval_semantics": "[valid_from, valid_to)",
    }
