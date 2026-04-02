# app/model/download/transfer.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from app.model.core.config.config import AppConfig
from app.model.core.domain.errors import OperationCancelled
from app.model.core.utils.string_utils import sanitize_filename, sanitize_url_for_log
from app.model.download.artifacts import DownloadArtifactManager
from app.model.download.domain import DownloadError, ExtractorAccessContext, SourceAccessInterventionRequired
from app.model.download.gateway import YtdlpGateway
from app.model.download.inventory import TrackInventory
from app.model.download.plan import DownloadPlanBuilder
from app.model.download.policy import DownloadPolicy
from app.model.download.strategy import resolve_extractor_strategy

from .access import access_intervention_request_from_meta, resolve_source_access_context, validate_cookie_context
from .probe import probe, raise_if_probe_blocks_download

_LOG = logging.getLogger(__name__)


def emit_download_progress(
    progress_cb: Callable[[int, str], None] | None,
    *,
    pct: int,
    status: str,
) -> None:
    """Emit normalized download progress when a callback is available."""

    if not progress_cb:
        return
    try:
        progress_cb(int(max(0, min(100, int(pct)))), str(status or ""))
    except (RuntimeError, TypeError, ValueError):
        return


def download_progress_pct(payload: dict[str, Any]) -> int:
    """Extract a normalized download progress percentage from a yt_dlp payload."""

    raw_pct = str(payload.get("_percent_str") or "").strip().replace("%", "")
    if raw_pct:
        try:
            return int(max(0.0, min(100.0, float(raw_pct))))
        except (TypeError, ValueError, OverflowError):
            return 0

    downloaded = payload.get("downloaded_bytes") or 0
    total = payload.get("total_bytes") or payload.get("total_bytes_estimate") or 0
    try:
        if total:
            return int(max(0.0, min(100.0, (float(downloaded) / float(total)) * 100.0)))
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return 0
    return 0


def build_download_hooks(
    *,
    progress_cb: Callable[[int, str], None] | None,
    cancel_check: Callable[[], bool] | None,
) -> tuple[Callable[[dict[str, Any]], None], Callable[[dict[str, Any]], None]]:
    """Build yt_dlp progress and postprocessor hooks for one download."""

    def _hook(payload: dict[str, Any]) -> None:
        if cancel_check and cancel_check():
            raise OperationCancelled()

        status = str(payload.get("status") or "").strip().lower()
        if status == "downloading":
            emit_download_progress(
                progress_cb,
                pct=download_progress_pct(payload),
                status="downloading",
            )
            return
        if status == "finished":
            emit_download_progress(progress_cb, pct=100, status="downloaded")

    def _post_hook(payload: dict[str, Any]) -> None:
        if cancel_check and cancel_check():
            raise OperationCancelled()

        status = str(payload.get("status") or "").strip().lower()
        if status == "started":
            emit_download_progress(progress_cb, pct=100, status="postprocessing")
            return
        if status == "finished":
            emit_download_progress(progress_cb, pct=100, status="postprocessed")

    return _hook, _post_hook


def available_track_probe_clients(
    selected_audio_track: dict[str, Any],
    *,
    meta: dict[str, Any] | None,
) -> tuple[str, ...]:
    """Return probe clients relevant for one selected audio track."""

    ordered_clients = list(TrackInventory.ordered_probe_clients_for_track(selected_audio_track))
    if not TrackInventory.probe_variants_from_meta(meta) and "default" not in ordered_clients:
        ordered_clients.append("default")
    return tuple(ordered_clients)


def ordered_track_download_clients(
    selected_audio_track: dict[str, Any],
    *,
    meta: dict[str, Any] | None,
    extractor_context: ExtractorAccessContext,
) -> tuple[str, ...]:
    """Return download clients ordered for the selected audio track and extractor context."""

    candidate_clients = available_track_probe_clients(selected_audio_track, meta=meta)
    strategy = resolve_extractor_strategy(extractor_context.extractor_key)
    ordered_clients = strategy.select_download_clients(extractor_context, candidate_clients)
    return ordered_clients or candidate_clients


def download(
    *,
    url: str,
    kind: str,
    quality: str,
    ext: str,
    out_dir: Path,
    progress_cb: Callable[[int, str], None] | None = None,
    audio_track_id: str | None = None,
    file_stem: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
    purpose: str = DownloadPolicy.DOWNLOAD_DEFAULT_PURPOSE,
    keep_output: bool = True,
    meta: dict[str, Any] | None = None,
    browser_cookies_mode_override: str | None = None,
    cookie_file_override: str | None = None,
    browser_policy_override: str | None = None,
    access_mode_override: str | None = None,
) -> Path | None:
    """Download one remote source into its final or staging artifact."""

    min_h = AppConfig.downloader_min_video_height()
    max_h = AppConfig.downloader_max_video_height()
    ext_l = (ext or "").lower().strip().lstrip(".")
    purpose_l = str(purpose or DownloadPolicy.DOWNLOAD_DEFAULT_PURPOSE).strip().lower()
    contract = DownloadPolicy.resolve_download_contract(
        kind=kind,
        purpose=purpose_l,
        keep_output=bool(keep_output),
        ext=ext_l,
    )
    plan_ext = str(contract.get("plan_ext") or "").strip().lower()
    final_ext = str(contract.get("final_ext") or "").strip().lower()
    artifact_policy = str(
        contract.get("artifact_policy") or DownloadPolicy.DOWNLOAD_ARTIFACT_POLICY_STRICT_FINAL_EXT
    ).strip().lower()

    audio_track_id_norm = str(audio_track_id or "").strip() or None
    lang_base = ""

    if meta is None:
        try:
            meta = probe(
                url,
                browser_cookies_mode_override=browser_cookies_mode_override,
                cookie_file_override=cookie_file_override,
                browser_policy_override=browser_policy_override,
                access_mode_override=access_mode_override,
                interactive=True,
            )
        except DownloadError as ex:
            err_key = str(ex.key or "").strip()
            if audio_track_id_norm or err_key in {
                "error.download.authentication_required",
                "error.download.browser_cookies_unavailable",
                "error.download.cookie_file_invalid",
                "error.download.extended_access_required",
            }:
                raise
            meta = None

    source_access_context = resolve_source_access_context(
        url,
        operation=DownloadPolicy.DOWNLOAD_OPERATION_DOWNLOAD,
        browser_cookies_mode_override=browser_cookies_mode_override,
        cookie_file_override=cookie_file_override,
        browser_policy_override=browser_policy_override,
        access_mode_override=access_mode_override,
        interactive=True,
    )
    cookie_context = source_access_context.cookie_context
    extractor_context = source_access_context.extractor_context
    selected_probe_client = str(extractor_context.client or "").strip().lower() or "default"
    access_request = access_intervention_request_from_meta(meta)
    if access_request is not None:
        current_mode = DownloadPolicy.normalize_extractor_access_mode(extractor_context.access_mode)
        suggested_mode = DownloadPolicy.normalize_extractor_access_mode(access_request.suggested_access_mode)
        explicit_mode = (
            DownloadPolicy.normalize_extractor_access_mode(access_mode_override)
            if access_mode_override
            else ""
        )
        should_raise_access_request = not (explicit_mode and explicit_mode == current_mode)
        if should_raise_access_request and suggested_mode != current_mode:
            raise SourceAccessInterventionRequired(access_request)
    raise_if_probe_blocks_download(meta, cookie_context=cookie_context)

    selected_audio_track = None
    if audio_track_id_norm:
        selected_audio_track = TrackInventory.find_audio_track(meta, audio_track_id_norm)
        if selected_audio_track is None:
            raise DownloadError(
                "error.download.download_failed",
                detail="selected audio track is no longer available",
            )

    if selected_audio_track is not None:
        ordered_probe_clients = ordered_track_download_clients(
            selected_audio_track,
            meta=meta,
            extractor_context=extractor_context,
        )
        plan, selected_probe_client = DownloadPlanBuilder.build_explicit_plan(
            kind=kind,
            quality=quality,
            plan_ext=plan_ext,
            lang_base=lang_base,
            selected_audio_track=selected_audio_track,
            ordered_probe_clients=ordered_probe_clients,
            purpose=purpose_l,
            keep_output=bool(keep_output),
            meta=meta,
            min_h=min_h,
            max_h=max_h,
        )
        source_access_context = source_access_context.with_client(selected_probe_client)
    else:
        if kind == "audio":
            plan = DownloadPlanBuilder.build_audio_plan(
                info=meta,
                quality=quality,
                ext_l=plan_ext,
                lang_base=lang_base,
                selected_audio_track=None,
                purpose=purpose_l,
                keep_output=bool(keep_output),
            )
        else:
            plan = DownloadPlanBuilder.build_video_plan(
                info=meta,
                quality=quality,
                ext_l=plan_ext,
                lang_base=lang_base,
                selected_audio_track=None,
                purpose=purpose_l,
                keep_output=bool(keep_output),
                min_h=min_h,
                max_h=max_h,
            )

    progress_hook, post_hook = build_download_hooks(progress_cb=progress_cb, cancel_check=cancel_check)
    stem = sanitize_filename(file_stem or "%(title)s") or DownloadPolicy.DOWNLOAD_DEFAULT_STEM
    stage_dir = DownloadArtifactManager.create_download_stage(stem=stem)
    outtmpl = DownloadArtifactManager.build_stage_outtmpl(stage_dir=stage_dir, stem=stem)

    validate_cookie_context(cookie_context)
    ydl_opts: dict[str, Any] = YtdlpGateway.base_ydl_opts(
        url=url,
        quiet=not _LOG.isEnabledFor(logging.DEBUG),
        skip_download=False,
        cookie_context=cookie_context,
        source_access_context=source_access_context,
    )
    ydl_opts.update(
        {
            "format": plan.get("format")
            or (
                DownloadPolicy.DOWNLOAD_FALLBACK_AUDIO_SELECTOR
                if kind == "audio"
                else DownloadPolicy.DOWNLOAD_FALLBACK_VIDEO_SELECTOR
            ),
            "outtmpl": outtmpl,
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [post_hook],
            "postprocessors": list(plan.get("postprocessors") or []),
        }
    )
    format_sort = list(plan.get("format_sort") or [])
    if format_sort:
        ydl_opts["format_sort"] = format_sort
    merge_output_format = str(plan.get("merge_output_format") or "").strip().lower()
    if merge_output_format:
        ydl_opts["merge_output_format"] = merge_output_format

    _LOG.debug(
        (
            "Download started. url=%s kind=%s quality=%s ext=%s audio_track_id=%s purpose=%s "
            "keep_output=%s probe_client=%s final_out_dir=%s stage_dir=%s stem=%s plan=%s"
        ),
        sanitize_url_for_log(url),
        kind,
        quality,
        ext_l,
        audio_track_id_norm or "",
        purpose_l,
        bool(keep_output),
        selected_probe_client,
        out_dir,
        stage_dir,
        stem,
        {
            "format": ydl_opts.get("format"),
            "format_sort": format_sort,
            "merge_output_format": merge_output_format,
            "postprocessors": ydl_opts.get("postprocessors"),
            "extractor_args": ydl_opts.get("extractor_args"),
            "plan_ext": plan_ext,
            "final_ext": final_ext,
            "artifact_policy": artifact_policy,
        },
    )

    info: dict[str, Any] | None = None
    try:
        info, download_runtime = YtdlpGateway.extract_info_with_fallback(
            url=url,
            ydl_opts=ydl_opts,
            download=True,
            allow_cookie_intervention=True,
        )
        if download_runtime.get("js_runtime_fallback"):
            _LOG.info(
                "Download continued after JS runtime fallback. url=%s detail=%s",
                sanitize_url_for_log(url),
                str(download_runtime.get("js_runtime_error") or ""),
            )

        stage_files = DownloadArtifactManager.stage_files(stage_dir)
        _LOG.debug(
            (
                "Download postprocess state. url=%s requested_ext=%s info_ext=%s "
                "info_filepath=%s stage_dir=%s stage_files=%s"
            ),
            sanitize_url_for_log(url),
            ext_l,
            DownloadArtifactManager.normalize_ext((info or {}).get("ext")),
            str((info or {}).get("filepath") or (info or {}).get("_filename") or ""),
            str(stage_dir),
            [path.name for path in stage_files],
        )

        artifact = DownloadArtifactManager.resolve_stage_artifact(
            info=info,
            stage_dir=stage_dir,
            stem=stem,
            requested_ext=final_ext,
            artifact_policy=artifact_policy,
        )
        if artifact is None:
            _LOG.warning(
                (
                    "Download finished without stage artifact. url=%s requested_ext=%s final_ext=%s "
                    "artifact_policy=%s info_ext=%s stage_dir=%s stage_files=%s"
                ),
                sanitize_url_for_log(url),
                ext_l,
                final_ext,
                artifact_policy,
                DownloadArtifactManager.normalize_ext((info or {}).get("ext")),
                str(stage_dir),
                [path.name for path in stage_files],
            )
            DownloadArtifactManager.cleanup_stage_dir(stage_dir)
            raise DownloadError(
                "error.download.download_failed",
                detail="download finished without a final stage artifact",
            )

        should_promote = purpose_l == DownloadPolicy.DOWNLOAD_PURPOSE_DOWNLOAD or bool(keep_output)
        if should_promote:
            promoted = DownloadArtifactManager.promote_stage_artifact(
                artifact=artifact,
                final_dir=out_dir,
                stem=stem,
                requested_ext=final_ext,
            )
            DownloadArtifactManager.cleanup_stage_dir(stage_dir)
            _LOG.info(
                (
                    "Download finished. url=%s requested_ext=%s final_ext=%s artifact_policy=%s "
                    "resolved_artifact=%s promoted=%s"
                ),
                sanitize_url_for_log(url),
                ext_l,
                final_ext,
                artifact_policy,
                artifact.name,
                promoted.name,
            )
            return promoted

        _LOG.info(
            "Download finished in staging. url=%s requested_ext=%s final_ext=%s artifact_policy=%s path=%s",
            sanitize_url_for_log(url),
            ext_l,
            final_ext,
            artifact_policy,
            artifact.name,
        )
        return artifact
    except OperationCancelled:
        stage_files = DownloadArtifactManager.stage_files(stage_dir)
        DownloadArtifactManager.cleanup_stage_dir(stage_dir)
        _LOG.debug(
            "Download cancelled. url=%s stage_dir=%s stage_files=%s",
            sanitize_url_for_log(url),
            str(stage_dir),
            [path.name for path in stage_files],
        )
        raise
    except DownloadError:
        DownloadArtifactManager.cleanup_stage_dir(stage_dir)
        raise
    except SourceAccessInterventionRequired:
        DownloadArtifactManager.cleanup_stage_dir(stage_dir)
        raise
    except Exception as ex:
        stage_files = DownloadArtifactManager.stage_files(stage_dir)
        DownloadArtifactManager.cleanup_stage_dir(stage_dir)
        network_key = YtdlpGateway.classify_network_error(ex)
        if network_key:
            YtdlpGateway.log_network_error(action="download", url=url, ex=ex)
            raise DownloadError(network_key)
        _LOG.debug(
            (
                "Download failed. url=%s requested_ext=%s final_ext=%s artifact_policy=%s info_ext=%s "
                "info_filepath=%s stage_dir=%s stage_files=%s detail=%s"
            ),
            sanitize_url_for_log(url),
            ext_l,
            final_ext,
            artifact_policy,
            DownloadArtifactManager.normalize_ext((info or {}).get("ext")),
            str((info or {}).get("filepath") or (info or {}).get("_filename") or ""),
            str(stage_dir),
            [path.name for path in stage_files],
            str(ex),
        )
        raise DownloadError("error.download.download_failed", detail=str(ex))
