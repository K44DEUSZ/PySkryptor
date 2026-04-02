# app/model/transcription/service.py
from __future__ import annotations

from app.model.core.domain.entities import TranscriptionSessionRequest
from app.model.core.domain.results import SessionResult
from app.model.download.service import DownloadService
from app.model.engines.contracts import TranscriptionEngineProtocol, TranslationEngineProtocol
from app.model.transcription.errors import TranscriptionError
from app.model.translation.service import TranslationService

from .materialize import DownloadUseCaseProtocol, cleanup_downloaded_sources, materialize_work_items
from .processing import process_materialized_work_item
from .session import (
    AccessInterventionResolverFn,
    CancelCheckFn,
    ConflictResolverFn,
    ItemErrorFn,
    ItemOutputDirFn,
    ItemPathUpdateFn,
    ItemProgressFn,
    ItemStatusFn,
    ProgressFn,
    SourceEntry,
    TranscriptReadyFn,
    build_session_callbacks,
    finish_session,
    finish_session_if_no_work,
    log_session_plan,
    prepare_session_runtime,
)


class TranscriptionService:
    """Run batch transcription sessions using process-backed ASR and translation engines."""

    def __init__(
        self,
        *,
        transcription_engine: TranscriptionEngineProtocol,
        translation_engine: TranslationEngineProtocol,
    ) -> None:
        self._transcription_engine = transcription_engine
        self._translation = TranslationService(translation_engine=translation_engine)
        download = DownloadService()
        assert isinstance(download, DownloadUseCaseProtocol)
        self._download: DownloadUseCaseProtocol = download

    def run_session(
        self,
        *,
        entries: list[SourceEntry],
        session_request: TranscriptionSessionRequest,
        progress: ProgressFn,
        item_status: ItemStatusFn,
        item_progress: ItemProgressFn,
        item_path_update: ItemPathUpdateFn,
        transcript_ready: TranscriptReadyFn,
        item_error: ItemErrorFn,
        item_output_dir: ItemOutputDirFn,
        conflict_resolver: ConflictResolverFn,
        access_intervention_resolver: AccessInterventionResolverFn | None,
        cancel_check: CancelCheckFn,
    ) -> SessionResult:
        entries = list(entries or [])
        callbacks = build_session_callbacks(
            item_status=item_status,
            item_progress=item_progress,
            item_path_update=item_path_update,
            transcript_ready=transcript_ready,
            item_error=item_error,
            item_output_dir=item_output_dir,
            conflict_resolver=conflict_resolver,
            access_intervention_resolver=access_intervention_resolver,
            cancel_check=cancel_check,
        )
        runtime = prepare_session_runtime(
            entries=entries,
            session_request=session_request,
            progress=progress,
        )

        log_session_plan(runtime=runtime, entries=entries)
        materialized = materialize_work_items(
            entries=entries,
            runtime=runtime,
            callbacks=callbacks,
            download_service=self._download,
            error_factory=lambda key, **params: TranscriptionError(key, **params),
        )
        processed_any = False
        had_errors = bool(materialized.had_errors)
        was_cancelled = bool(materialized.was_cancelled)

        early_finish = finish_session_if_no_work(
            runtime=runtime,
            materialized=materialized,
            cleanup_downloads=cleanup_downloaded_sources,
        )
        if early_finish is not None:
            return early_finish

        apply_all: tuple[str, str] | None = None
        for work_item in materialized.work:
            item_result = process_materialized_work_item(
                transcription_engine=self._transcription_engine,
                translation_service=self._translation,
                work_item=work_item,
                apply_all=apply_all,
                runtime=runtime,
                callbacks=callbacks,
            )
            processed_any = bool(processed_any or item_result.processed_any)
            had_errors = bool(had_errors or item_result.had_errors)
            apply_all = item_result.apply_all
            if item_result.was_cancelled:
                was_cancelled = True
                break

        return finish_session(
            runtime=runtime,
            processed_any=processed_any,
            had_errors=had_errors,
            was_cancelled=was_cancelled,
            cleanup_downloads=cleanup_downloaded_sources,
        )
