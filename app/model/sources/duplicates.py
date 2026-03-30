# app/model/sources/duplicates.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class SourceDuplicateRecord:
    """Single source occurrence evaluated by the shared duplicate policy."""

    source_key: str
    is_terminal: bool


@dataclass(frozen=True)
class DuplicateDecision:
    """Resolved duplicate decision for one candidate source key."""

    allow: bool
    duplicate: bool
    has_active_duplicate: bool
    has_terminal_duplicate: bool


def evaluate_source_duplicate(
    records: Iterable[SourceDuplicateRecord],
    candidate_source_key: str,
) -> DuplicateDecision:
    """Resolve whether the candidate source key can be added under shared queue rules."""

    target = str(candidate_source_key or "").strip()
    if not target:
        return DuplicateDecision(
            allow=False,
            duplicate=False,
            has_active_duplicate=False,
            has_terminal_duplicate=False,
        )

    has_active_duplicate = False
    has_terminal_duplicate = False
    for record in records:
        if str(record.source_key or "").strip() != target:
            continue
        if bool(record.is_terminal):
            has_terminal_duplicate = True
            continue
        has_active_duplicate = True
        break

    return DuplicateDecision(
        allow=not has_active_duplicate,
        duplicate=bool(has_active_duplicate or has_terminal_duplicate),
        has_active_duplicate=has_active_duplicate,
        has_terminal_duplicate=has_terminal_duplicate,
    )
