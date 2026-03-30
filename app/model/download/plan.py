# app/model/download/plan.py
from __future__ import annotations

import re
from typing import Any

from app.model.download.policy import DownloadPolicy
from app.model.download.inventory import TrackInventory
from app.model.download.domain import DownloadError


class DownloadPlanBuilder:
    """Build deterministic yt_dlp selectors and post-processing plans."""

    @staticmethod
    def parse_video_quality_height(quality: str) -> int | None:
        quality_normalized = str(quality or "").strip().lower()
        if not quality_normalized or quality_normalized == "auto":
            return None

        match = re.fullmatch(r"(\d{3,4})p?", quality_normalized)
        if not match:
            return None

        try:
            value = int(match.group(1))
        except ValueError:
            return None
        return value if value > 0 else None

    @staticmethod
    def height_filter(*, min_h: int | None = None, max_h: int | None = None) -> str:
        parts: list[str] = []
        if isinstance(min_h, int) and min_h > 0:
            parts.append(f"[height>={min_h}]")
        if isinstance(max_h, int) and max_h > 0:
            parts.append(f"[height<={max_h}]")
        return "".join(parts)

    @staticmethod
    def video_format_selector(*, min_h: int | None, max_h: int | None, target_h: int | None, lang_base: str) -> str:
        if isinstance(target_h, int) and target_h > 0:
            video_filter = DownloadPlanBuilder.height_filter(max_h=target_h)
            video_selector = f"bv*{video_filter}"
            merged_selector = f"b{video_filter}"
        else:
            video_filter = DownloadPlanBuilder.height_filter(min_h=min_h, max_h=max_h)
            video_selector = f"bv*{video_filter}" if video_filter else "bv*"
            merged_selector = "b"

        if lang_base:
            return f"{video_selector}+ba[language^={lang_base}]/{video_selector}+ba/{merged_selector}"
        return f"{video_selector}+ba/{merged_selector}"

    @staticmethod
    def audio_track_has_audio_only_ext(track: dict[str, Any] | None, *extensions: str) -> bool:
        wanted = {str(ext or "").strip().lower() for ext in extensions if str(ext or "").strip()}
        if not wanted:
            return False
        for candidate in list((track or {}).get("candidates") or []):
            if bool(candidate.get("has_video")):
                continue
            if str(candidate.get("ext") or "").strip().lower() in wanted:
                return True
        return False

    @staticmethod
    def ordered_audio_track_candidates(
        track: dict[str, Any] | None,
        *,
        preferred_extensions: tuple[str, ...] = (),
        require_audio_only: bool = False,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for candidate in list((track or {}).get("candidates") or []):
            if not isinstance(candidate, dict) or not bool(candidate.get("has_audio")):
                continue
            if require_audio_only and bool(candidate.get("has_video")):
                continue
            candidates.append(dict(candidate))
        return sorted(
            candidates,
            key=lambda item: TrackInventory.audio_track_candidate_sort_key(
                item,
                preferred_extensions=preferred_extensions,
            ),
        )

    @staticmethod
    def selector_join(parts: list[str]) -> str:
        normalized = [str(part or "").strip() for part in parts if str(part or "").strip()]
        return "/".join(dict.fromkeys(normalized))

    @staticmethod
    def video_candidate(fmt: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(fmt, dict) or not TrackInventory.has_video(fmt):
            return None

        format_id = str(fmt.get("format_id") or "").strip()
        if not format_id:
            return None

        return {
            "format_id": format_id,
            "ext": str(fmt.get("ext") or "").strip().lower(),
            "height": TrackInventory.coerce_track_metric(fmt.get("height")),
            "tbr": TrackInventory.coerce_track_metric(fmt.get("tbr")),
            "has_audio": bool(TrackInventory.has_audio(fmt)),
            "has_video": True,
        }

    @staticmethod
    def video_candidate_sort_key(
        candidate: dict[str, Any],
        *,
        preferred_extensions: tuple[str, ...] = (),
    ) -> tuple[Any, ...]:
        preferred = {
            str(ext or "").strip().lower() for ext in preferred_extensions if str(ext or "").strip()
        }
        ext = str(candidate.get("ext") or "").strip().lower()
        height = int(candidate.get("height") or 0)
        tbr = int(candidate.get("tbr") or 0)
        return (
            0 if ext in preferred else 1,
            -height,
            -tbr,
            ext,
            str(candidate.get("format_id") or ""),
        )

    @staticmethod
    def ordered_video_candidates(
        info: dict[str, Any] | None,
        *,
        preferred_extensions: tuple[str, ...] = (),
        require_audio: bool | None = None,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for fmt in TrackInventory.formats(info):
            candidate = DownloadPlanBuilder.video_candidate(fmt)
            if candidate is None:
                continue
            if require_audio is not None and bool(candidate.get("has_audio")) != bool(require_audio):
                continue
            candidates.append(candidate)
        return sorted(
            candidates,
            key=lambda item: DownloadPlanBuilder.video_candidate_sort_key(
                item,
                preferred_extensions=preferred_extensions,
            ),
        )

    @staticmethod
    def matches_height(
        candidate: dict[str, Any],
        *,
        min_h: int | None,
        max_h: int | None,
        target_h: int | None,
        exact: bool,
    ) -> bool:
        height = int(candidate.get("height") or 0)
        if height <= 0:
            return False
        if isinstance(target_h, int) and target_h > 0 and exact:
            return height == target_h
        if isinstance(min_h, int) and min_h > 0 and height < min_h:
            return False
        resolved_max = target_h if isinstance(target_h, int) and target_h > 0 else None
        if isinstance(max_h, int) and max_h > 0:
            resolved_max = min(resolved_max, max_h) if isinstance(resolved_max, int) and resolved_max > 0 else max_h
        if isinstance(resolved_max, int) and 0 < resolved_max < height:
            return False
        return True

    @staticmethod
    def explicit_audio_selector(
        track: dict[str, Any] | None,
        *,
        preferred_extensions: tuple[str, ...] = (),
    ) -> str:
        candidates = DownloadPlanBuilder.ordered_audio_track_candidates(
            track,
            preferred_extensions=preferred_extensions,
        )
        preferred = {
            str(ext or "").strip().lower() for ext in preferred_extensions if str(ext or "").strip()
        }
        if preferred:
            preferred_candidates = [
                candidate
                for candidate in candidates
                if str(candidate.get("ext") or "").strip().lower() in preferred
            ]
            audio_only_candidates = [
                candidate for candidate in preferred_candidates if not bool(candidate.get("has_video"))
            ]
            candidates = audio_only_candidates or preferred_candidates
        return DownloadPlanBuilder.selector_join([str(candidate.get("format_id") or "") for candidate in candidates])

    @staticmethod
    def explicit_video_selector(
        info: dict[str, Any] | None,
        *,
        track: dict[str, Any] | None,
        video_extensions: tuple[str, ...] = (),
        audio_extensions: tuple[str, ...] = (),
        min_h: int | None,
        max_h: int | None,
        target_h: int | None,
    ) -> str:
        preferred_video_extensions = {
            str(ext or "").strip().lower() for ext in video_extensions if str(ext or "").strip()
        }
        preferred_audio_extensions = {
            str(ext or "").strip().lower() for ext in audio_extensions if str(ext or "").strip()
        }

        combined_candidates = [
            candidate
            for candidate in DownloadPlanBuilder.ordered_audio_track_candidates(
                track,
                preferred_extensions=tuple(preferred_video_extensions),
            )
            if bool(candidate.get("has_video"))
        ]
        if preferred_video_extensions:
            combined_candidates = [
                candidate
                for candidate in combined_candidates
                if str(candidate.get("ext") or "").strip().lower() in preferred_video_extensions
            ]

        pair_audio_candidates = DownloadPlanBuilder.ordered_audio_track_candidates(
            track,
            preferred_extensions=tuple(preferred_audio_extensions),
            require_audio_only=True,
        )
        if preferred_audio_extensions:
            pair_audio_candidates = [
                candidate
                for candidate in pair_audio_candidates
                if str(candidate.get("ext") or "").strip().lower() in preferred_audio_extensions
            ]

        video_candidates = DownloadPlanBuilder.ordered_video_candidates(
            info,
            preferred_extensions=tuple(preferred_video_extensions),
            require_audio=False,
        )
        if preferred_video_extensions:
            video_candidates = [
                candidate
                for candidate in video_candidates
                if str(candidate.get("ext") or "").strip().lower() in preferred_video_extensions
            ]

        selectors: list[str] = []

        def _extend(*, exact: bool) -> None:
            for candidate in combined_candidates:
                if DownloadPlanBuilder.matches_height(
                    candidate,
                    min_h=min_h,
                    max_h=max_h,
                    target_h=target_h,
                    exact=exact,
                ):
                    selectors.append(str(candidate.get("format_id") or ""))

            for video_candidate in video_candidates:
                if not DownloadPlanBuilder.matches_height(
                    video_candidate,
                    min_h=min_h,
                    max_h=max_h,
                    target_h=target_h,
                    exact=exact,
                ):
                    continue
                for audio_candidate in pair_audio_candidates:
                    selectors.append(
                        f"{str(video_candidate.get('format_id') or '').strip()}+"
                        f"{str(audio_candidate.get('format_id') or '').strip()}"
                    )

        if isinstance(target_h, int) and target_h > 0:
            _extend(exact=True)
        _extend(exact=False)
        return DownloadPlanBuilder.selector_join(selectors)

    @staticmethod
    def has_audio_only_ext(info: dict[str, Any] | None, *extensions: str) -> bool:
        wanted = {str(ext or "").strip().lower() for ext in extensions if str(ext or "").strip()}
        if not wanted:
            return False
        for fmt in TrackInventory.formats(info):
            if not TrackInventory.has_audio(fmt) or TrackInventory.has_video(fmt):
                continue
            if str(fmt.get("ext") or "").strip().lower() in wanted:
                return True
        return False

    @staticmethod
    def has_combined_ext(info: dict[str, Any] | None, ext: str) -> bool:
        wanted = str(ext or "").strip().lower()
        if not wanted:
            return False
        for fmt in TrackInventory.formats(info):
            if not (TrackInventory.has_audio(fmt) and TrackInventory.has_video(fmt)):
                continue
            if str(fmt.get("ext") or "").strip().lower() == wanted:
                return True
        return False

    @staticmethod
    def has_video_only_ext(info: dict[str, Any] | None, ext: str) -> bool:
        wanted = str(ext or "").strip().lower()
        if not wanted:
            return False
        for fmt in TrackInventory.formats(info):
            if not TrackInventory.has_video(fmt) or TrackInventory.has_audio(fmt):
                continue
            if str(fmt.get("ext") or "").strip().lower() == wanted:
                return True
        return False

    @staticmethod
    def audio_selector(*, lang_base: str, extensions: tuple[str, ...] = ()) -> str:
        selectors: list[str] = []
        if extensions:
            for ext in extensions:
                ext_normalized = str(ext or "").strip().lower()
                if not ext_normalized:
                    continue
                if lang_base:
                    selectors.append(f"ba[ext={ext_normalized}][language^={lang_base}]")
                selectors.append(f"ba[ext={ext_normalized}]")
        if lang_base:
            selectors.append(f"ba[language^={lang_base}]")
        selectors.extend(str(DownloadPolicy.DOWNLOAD_FALLBACK_AUDIO_SELECTOR or "").split("/"))
        return "/".join(dict.fromkeys(selectors))

    @staticmethod
    def video_target_selector(
        *,
        min_h: int | None,
        max_h: int | None,
        target_h: int | None,
        target_ext: str,
        lang_base: str,
        audio_extensions: tuple[str, ...] = (),
    ) -> str:
        target_ext_normalized = str(target_ext or "").strip().lower()
        audio_selector = DownloadPlanBuilder.audio_selector(
            lang_base=lang_base,
            extensions=tuple(audio_extensions or ()),
        )
        selectors: list[str] = []

        def _append(video_filter: str) -> None:
            video_selector = f"bv*[ext={target_ext_normalized}]{video_filter}"
            combined_selector = f"b[ext={target_ext_normalized}]{video_filter}"
            selectors.append(f"{video_selector}+{audio_selector}")
            selectors.append(combined_selector)

        if isinstance(target_h, int) and target_h > 0:
            _append(f"[height={target_h}]")
            _append(DownloadPlanBuilder.height_filter(min_h=min_h, max_h=target_h))
        else:
            _append(DownloadPlanBuilder.height_filter(min_h=min_h, max_h=max_h))

        return "/".join(dict.fromkeys(selectors))

    @staticmethod
    def build_audio_plan(
        *,
        info: dict[str, Any] | None,
        quality: str,
        ext_l: str,
        lang_base: str,
        selected_audio_track: dict[str, Any] | None = None,
        purpose: str,
        keep_output: bool,
    ) -> dict[str, Any]:
        profile = DownloadPolicy.download_audio_format_profile(ext_l)
        selector_extensions = DownloadPolicy.download_audio_selector_extensions(ext_l)
        preferred_codec = str(profile.get("preferredcodec") or ext_l or "").strip().lower()

        plan: dict[str, Any] = {
            "format": DownloadPlanBuilder.audio_selector(lang_base=lang_base),
            "format_sort": ["lang", "acodec", "abr:desc", "tbr:desc"],
            "postprocessors": [],
            "merge_output_format": None,
        }

        if selected_audio_track is not None:
            explicit_selector = DownloadPlanBuilder.explicit_audio_selector(
                selected_audio_track,
                preferred_extensions=selector_extensions,
            )
            if not explicit_selector:
                explicit_selector = DownloadPlanBuilder.explicit_audio_selector(selected_audio_track)
            if not explicit_selector:
                raise DownloadError(
                    "error.down.download_failed",
                    detail="no matching format found for selected audio track",
                )
            plan["format"] = explicit_selector
            plan["format_sort"] = []

        if purpose == DownloadPolicy.DOWNLOAD_PURPOSE_TRANSCRIPTION and not keep_output:
            return plan

        if (
            selected_audio_track is None
            and selector_extensions
            and DownloadPlanBuilder.has_audio_only_ext(info, *selector_extensions)
        ):
            plan["format"] = DownloadPlanBuilder.audio_selector(
                lang_base=lang_base,
                extensions=selector_extensions,
            )
            return plan
        if (
            selected_audio_track is not None
            and selector_extensions
            and DownloadPlanBuilder.audio_track_has_audio_only_ext(
                selected_audio_track,
                *selector_extensions,
            )
        ):
            return plan

        if preferred_codec and ext_l and ext_l not in {"", "auto"}:
            plan["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": preferred_codec,
                    "preferredquality": str(quality or ""),
                }
            ]
        return plan

    @staticmethod
    def build_video_plan(
        *,
        info: dict[str, Any] | None,
        quality: str,
        ext_l: str,
        lang_base: str,
        selected_audio_track: dict[str, Any] | None = None,
        purpose: str,
        keep_output: bool,
        min_h: int | None,
        max_h: int | None,
    ) -> dict[str, Any]:
        target_h = DownloadPlanBuilder.parse_video_quality_height(quality)
        if isinstance(max_h, int) and max_h > 0 and isinstance(target_h, int) and target_h > 0:
            target_h = min(target_h, max_h)
        if isinstance(min_h, int) and min_h > 0 and isinstance(target_h, int) and target_h > 0:
            target_h = max(target_h, min_h)
        profile = DownloadPolicy.download_video_format_profile(ext_l)
        video_extensions = DownloadPolicy.download_video_target_extensions(ext_l)
        audio_extensions = DownloadPolicy.download_video_audio_extensions(ext_l)
        strategy = str(profile.get("strategy") or "").strip().lower()
        strict_final_ext = bool(profile.get("strict_final_ext"))
        target_video_ext = str((video_extensions or (ext_l,))[0] or ext_l).strip().lower()

        plan: dict[str, Any] = {
            "format": DownloadPlanBuilder.video_format_selector(
                min_h=min_h,
                max_h=max_h,
                target_h=target_h,
                lang_base=lang_base,
            ),
            "format_sort": ([f"height:{target_h}"] if target_h else []) + ["lang"],
            "postprocessors": [],
            "merge_output_format": None,
        }

        if selected_audio_track is not None:
            preferred_video_extensions = (target_video_ext,) if ext_l and target_video_ext else tuple()
            preferred_selector = DownloadPlanBuilder.explicit_video_selector(
                info,
                track=selected_audio_track,
                video_extensions=preferred_video_extensions,
                audio_extensions=audio_extensions,
                min_h=min_h,
                max_h=max_h,
                target_h=target_h,
            )
            fallback_selector = DownloadPlanBuilder.explicit_video_selector(
                info,
                track=selected_audio_track,
                video_extensions=tuple(),
                audio_extensions=tuple(),
                min_h=min_h,
                max_h=max_h,
                target_h=target_h,
            )

            if purpose == DownloadPolicy.DOWNLOAD_PURPOSE_TRANSCRIPTION and not keep_output:
                plan["format"] = preferred_selector or fallback_selector
                plan["format_sort"] = []
                if not plan["format"]:
                    raise DownloadError(
                        "error.down.download_failed",
                        detail="no matching video format found for selected audio track",
                    )
                return plan

            if not ext_l or ext_l in {"", "auto"}:
                plan["format"] = fallback_selector
                plan["format_sort"] = []
                if not plan["format"]:
                    raise DownloadError(
                        "error.down.download_failed",
                        detail="no matching video format found for selected audio track",
                    )
                return plan

            if strategy in {"native_or_merge", "native_or_merge_or_convert"}:
                if preferred_selector:
                    plan["format"] = preferred_selector
                    plan["format_sort"] = []
                    if "+" in preferred_selector and ext_l:
                        plan["merge_output_format"] = ext_l
                    return plan
                if (strategy == "native_or_merge_or_convert" or strict_final_ext) and fallback_selector:
                    plan["format"] = fallback_selector
                    plan["format_sort"] = []
                    plan["postprocessors"] = [{"key": "FFmpegVideoConvertor", "preferedformat": ext_l}]
                    return plan
                raise DownloadError(
                    "error.down.download_failed",
                    detail="no matching video format found for selected audio track",
                )

            if strategy == "remux":
                plan["format"] = fallback_selector
                plan["format_sort"] = []
                if not plan["format"]:
                    raise DownloadError(
                        "error.down.download_failed",
                        detail="no matching video format found for selected audio track",
                    )
                plan["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": ext_l}]
                return plan

            if strategy == "convert":
                plan["format"] = fallback_selector
                plan["format_sort"] = []
                if not plan["format"]:
                    raise DownloadError(
                        "error.down.download_failed",
                        detail="no matching video format found for selected audio track",
                    )
                plan["postprocessors"] = [{"key": "FFmpegVideoConvertor", "preferedformat": ext_l}]
                return plan

            plan["format"] = fallback_selector
            plan["format_sort"] = []
            if not plan["format"]:
                raise DownloadError(
                    "error.down.download_failed",
                    detail="no matching video format found for selected audio track",
                )
            plan["postprocessors"] = [{"key": "FFmpegVideoConvertor", "preferedformat": ext_l}]
            return plan

        if purpose == DownloadPolicy.DOWNLOAD_PURPOSE_TRANSCRIPTION and not keep_output:
            return plan

        if not ext_l or ext_l in {"", "auto"}:
            return plan

        direct_combined = DownloadPlanBuilder.has_combined_ext(info, ext_l)
        direct_video = any(
            DownloadPlanBuilder.has_video_only_ext(info, candidate_ext)
            for candidate_ext in (video_extensions or (ext_l,))
        )
        has_audio_family = (
            DownloadPlanBuilder.has_audio_only_ext(info, *audio_extensions) if audio_extensions else False
        )

        if strategy in {"native_or_merge", "native_or_merge_or_convert"}:
            if direct_combined or (direct_video and has_audio_family):
                plan["format"] = DownloadPlanBuilder.video_target_selector(
                    min_h=min_h,
                    max_h=max_h,
                    target_h=target_h,
                    target_ext=target_video_ext,
                    lang_base=lang_base,
                    audio_extensions=audio_extensions,
                )
                if direct_video and has_audio_family:
                    plan["merge_output_format"] = ext_l
                return plan

            if strategy == "native_or_merge_or_convert" or strict_final_ext:
                plan["postprocessors"] = [{"key": "FFmpegVideoConvertor", "preferedformat": ext_l}]
            return plan

        if strategy == "remux":
            plan["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": ext_l}]
            return plan

        if strategy == "convert":
            plan["postprocessors"] = [{"key": "FFmpegVideoConvertor", "preferedformat": ext_l}]
            return plan

        plan["postprocessors"] = [{"key": "FFmpegVideoConvertor", "preferedformat": ext_l}]
        return plan

    @staticmethod
    def build_explicit_plan(
        *,
        kind: str,
        quality: str,
        plan_ext: str,
        lang_base: str,
        selected_audio_track: dict[str, Any],
        ordered_probe_clients: tuple[str, ...],
        purpose: str,
        keep_output: bool,
        meta: dict[str, Any] | None,
        min_h: int,
        max_h: int,
    ) -> tuple[dict[str, Any], str]:
        last_error: DownloadError | None = None
        ordered_clients = [
            str(client or "").strip().lower() or "default"
            for client in tuple(ordered_probe_clients or ())
            if str(client or "").strip()
        ]
        if not ordered_clients:
            raise DownloadError(
                "error.down.audio_track_probe_only",
                label=str(selected_audio_track.get("label") or selected_audio_track.get("lang_code") or ""),
            )

        for probe_client in ordered_clients:
            client_track = TrackInventory.track_for_probe_client(selected_audio_track, probe_client)
            if client_track is None:
                if probe_client == "default" and not TrackInventory.probe_variants_from_meta(meta):
                    client_track = dict(selected_audio_track)
                else:
                    continue

            client_meta = TrackInventory.info_for_probe_client(meta, probe_client) or meta
            try:
                if kind == "audio":
                    plan = DownloadPlanBuilder.build_audio_plan(
                        info=client_meta,
                        quality=quality,
                        ext_l=plan_ext,
                        lang_base=lang_base,
                        selected_audio_track=client_track,
                        purpose=purpose,
                        keep_output=bool(keep_output),
                    )
                else:
                    plan = DownloadPlanBuilder.build_video_plan(
                        info=client_meta,
                        quality=quality,
                        ext_l=plan_ext,
                        lang_base=lang_base,
                        selected_audio_track=client_track,
                        purpose=purpose,
                        keep_output=bool(keep_output),
                        min_h=min_h,
                        max_h=max_h,
                    )
            except DownloadError as ex:
                last_error = ex
                continue
            return plan, probe_client

        if last_error is not None:
            raise last_error

        detail = (
            "no matching format found for selected audio track"
            if kind == "audio"
            else "no matching video format found for selected audio track"
        )
        raise DownloadError("error.down.download_failed", detail=detail)
