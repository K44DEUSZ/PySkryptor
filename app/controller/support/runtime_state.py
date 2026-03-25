from __future__ import annotations

from typing import Any

from app.model.domain.runtime_state import AppRuntimeState


def build_runtime_state_payload(
    state: AppRuntimeState,
    *,
    pipeline: Any | None = None,
) -> dict[str, Any]:
    return {
        "transcription_ready": bool(state.transcription_ready and pipeline is not None),
        "transcription_error_key": state.transcription_error_key,
        "transcription_error_params": dict(state.transcription_error_params or {}),
        "translation_ready": bool(state.translation_ready),
        "translation_error_key": state.translation_error_key,
        "translation_error_params": dict(state.translation_error_params or {}),
    }
