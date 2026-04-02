# app/model/transcription/progress.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.model.sources.probe import is_url_source
from app.model.transcription.io import AudioExtractor

ProgressFn = Callable[[int], None]
SourceEntry = str | dict[str, Any]


@dataclass
class ItemPlan:
    """Per-item progress weighting used by the session progress tracker."""

    has_download: bool
    has_translate: bool
    weight: float
    stage_pct: dict[str, int]


class SessionProgressTracker:
    """Track global progress across session items and processing stages."""

    _STAGES = ("download", "preprocess", "transcribe", "translate", "save")
    _BASE_WEIGHTS = {"download": 0.10, "preprocess": 0.05, "transcribe": 0.60, "translate": 0.20, "save": 0.05}

    def __init__(self, progress_cb: ProgressFn) -> None:
        self._cb = progress_cb
        self._plans: dict[str, ItemPlan] = {}
        self._last_pct = 0

    def register(self, key: str, *, has_download: bool, has_translate: bool, weight: float = 1.0) -> None:
        self._plans[str(key)] = ItemPlan(
            has_download=bool(has_download),
            has_translate=bool(has_translate),
            weight=float(max(0.0001, weight)),
            stage_pct={stage: 0 for stage in self._STAGES},
        )

    def set_weight(self, key: str, *, weight: float) -> None:
        plan_key = str(key)
        if plan_key in self._plans:
            self._plans[plan_key].weight = float(max(0.0001, weight))

    def rename_key(self, old_key: str, new_key: str) -> None:
        old_plan_key = str(old_key)
        new_plan_key = str(new_key)
        if old_plan_key == new_plan_key:
            return
        plan = self._plans.pop(old_plan_key, None)
        if plan is None:
            return
        self._plans[new_plan_key] = plan

    def update(self, key: str, stage: str, pct: int) -> None:
        plan_key = str(key)
        if plan_key not in self._plans:
            return
        stage_name = str(stage)
        if stage_name not in self._STAGES:
            return
        self._plans[plan_key].stage_pct[stage_name] = int(max(0, min(100, pct)))
        self._emit()

    def mark_done(self, key: str) -> None:
        plan_key = str(key)
        if plan_key in self._plans:
            for stage in self._STAGES:
                self._plans[plan_key].stage_pct[stage] = 100
        self._emit()

    def _emit(self) -> None:
        if not self._plans:
            self._cb(0)
            return

        total_weight = 0.0
        total_progress = 0.0
        for plan in self._plans.values():
            weights = dict(self._BASE_WEIGHTS)
            if not plan.has_download:
                weights["download"] = 0.0
            if not plan.has_translate:
                weights["translate"] = 0.0

            norm = sum(weights.values()) or 1.0
            for stage in weights:
                weights[stage] = weights[stage] / norm

            item_progress = 0.0
            for stage, weight in weights.items():
                item_progress += (plan.stage_pct.get(stage, 0) / 100.0) * weight

            total_weight += plan.weight
            total_progress += item_progress * plan.weight

        pct = int(round((total_progress / max(0.0001, total_weight)) * 100))
        pct = max(0, min(100, pct))
        if pct < self._last_pct:
            pct = self._last_pct
        self._last_pct = pct
        self._cb(pct)


def entry_source_key(entry: SourceEntry) -> str:
    """Return the normalized key used to identify one queued source."""

    return str(entry.get("src") if isinstance(entry, dict) else entry)


def estimate_item_weight(key: str) -> float:
    """Estimate a source processing cost used for weighted global progress."""

    source_key = str(key or "")
    if not source_key or is_url_source(source_key):
        return 15.0
    try:
        path = Path(source_key)
    except (OSError, RuntimeError, TypeError, ValueError):
        return 1.0
    if not path.exists() or not path.is_file():
        return 1.0
    dur = AudioExtractor.probe_duration(path)
    if isinstance(dur, (int, float)) and dur > 0:
        return float(max(15.0, min(3600.0, float(dur))))
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    mb = float(size) / (1024.0 * 1024.0) if size else 0.0
    if mb > 0:
        return float(max(15.0, min(3600.0, mb * 10.0)))
    return 1.0


def register_session_entries(
    *,
    tracker: SessionProgressTracker,
    entries: list[SourceEntry],
    want_translate: bool,
) -> None:
    """Register queued items in the session progress tracker."""

    for entry in entries:
        key = entry_source_key(entry)
        tracker.register(
            key,
            has_download=is_url_source(key),
            has_translate=want_translate,
            weight=estimate_item_weight(key),
        )
