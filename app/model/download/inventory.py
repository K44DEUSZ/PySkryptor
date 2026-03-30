# app/model/download/inventory.py
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.model.core.utils.string_utils import is_youtube_url, normalize_lang_code
from app.model.download.gateway import YtdlpGateway
from app.model.download.policy import DownloadPolicy


class TrackLabelHeuristics:
    """Internal helpers for audio-track labels, roles, and signatures."""

    @staticmethod
    def info_is_youtube(info: dict[str, Any] | None) -> bool:
        if not isinstance(info, dict):
            return False

        extractor = str(info.get("extractor") or info.get("extractor_key") or "").strip().lower()
        if "youtube" in extractor:
            return True

        for key in ("webpage_url", "original_url", "url"):
            value = str(info.get(key) or "").strip()
            if value and is_youtube_url(value):
                return True
        return False

    @staticmethod
    def audio_track_payload(fmt: dict[str, Any]) -> dict[str, Any]:
        payload = fmt.get("audio_track")
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def audio_track_raw_id(fmt: dict[str, Any]) -> str:
        value = TrackLabelHeuristics.audio_track_payload(fmt).get("id")
        return value.strip() if isinstance(value, str) and value.strip() else ""

    @staticmethod
    def audio_track_display_name(fmt: dict[str, Any]) -> str:
        audio_track = TrackLabelHeuristics.audio_track_payload(fmt)
        for key in ("display_name", "displayName", "name"):
            value = audio_track.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def audio_track_is_default(fmt: dict[str, Any]) -> bool:
        audio_track = TrackLabelHeuristics.audio_track_payload(fmt)
        if audio_track.get("audioIsDefault") or audio_track.get("audio_is_default") or audio_track.get("default"):
            return True
        if not audio_track and TrackLabelHeuristics.audio_track_language_preference(fmt) == 5:
            return True
        tokens = TrackLabelHeuristics.audio_track_note_tokens(fmt, include_format=False)
        return any("(default)" in token.lower() for token in tokens)

    @staticmethod
    def audio_track_language_code(fmt: dict[str, Any]) -> str:
        audio_track = TrackLabelHeuristics.audio_track_payload(fmt)
        raw_lang = audio_track.get("lang_code") or audio_track.get("language") or audio_track.get("lang")
        if not raw_lang:
            raw_track_id = TrackLabelHeuristics.audio_track_raw_id(fmt)
            if raw_track_id:
                raw_lang = raw_track_id.split(".", 1)[0]
        if not raw_lang:
            raw_lang = fmt.get("language") or fmt.get("lang") or fmt.get("audio_lang")
        return normalize_lang_code(raw_lang, drop_region=False) or ""

    @staticmethod
    def audio_track_language_preference(fmt: dict[str, Any]) -> int:
        try:
            return int(fmt.get("language_preference") or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def split_audio_track_label_text(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        text = value.strip()
        if not text:
            return ""
        return text.split(",", 1)[0].strip() or text

    @staticmethod
    def audio_track_note_tokens(
        fmt: dict[str, Any],
        *,
        include_format: bool = True,
    ) -> list[str]:
        tokens: list[str] = []
        keys = ("format_note", "format") if include_format else ("format_note",)
        for key in keys:
            value = fmt.get(key)
            if not isinstance(value, str):
                continue
            for token in value.split(","):
                cleaned = token.strip()
                if cleaned:
                    tokens.append(cleaned)
        return tokens

    @staticmethod
    def looks_like_media_descriptor(value: str) -> bool:
        text = str(value or "").strip().lower()
        if not text:
            return False
        if re.fullmatch(r"\d{3,4}p(?:\d+)?", text):
            return True
        if re.fullmatch(r"\d+(?:\.\d+)?\s*(?:kbps|k)", text):
            return True
        if re.fullmatch(r"(?:audio|video)\s*\d{3,4}p", text):
            return True
        if re.fullmatch(r"\(\s*(?:default|original)\s*\)", text):
            return True
        return text in {
            "aac",
            "android",
            "av01",
            "avc1",
            "dashy",
            "damaged",
            "flac",
            "h264",
            "h265",
            "ios",
            "m4a",
            "medium",
            "missing pot",
            "mweb",
            "mp3",
            "mp4",
            "opus",
            "premium",
            "small",
            "tiny",
            "tv",
            "vorbis",
            "vp9",
            "web",
            "webm",
        }

    @staticmethod
    def youtube_audio_role_label(fmt: dict[str, Any]) -> str:
        note_text = " ".join(TrackLabelHeuristics.audio_track_note_tokens(fmt, include_format=False)).lower()
        display_name = TrackLabelHeuristics.audio_track_display_name(fmt).lower()
        combined_text = " ".join(part for part in (display_name, note_text) if part)
        audio_track = TrackLabelHeuristics.audio_track_payload(fmt)

        if "descriptive" in combined_text:
            return "descriptive"
        if "original" in combined_text:
            return "original"
        if (
            "(default)" in combined_text
            or audio_track.get("audioIsDefault")
            or audio_track.get("audio_is_default")
            or audio_track.get("default")
        ):
            return "default"
        return ""

    @staticmethod
    def audio_track_label_source(fmt: dict[str, Any], *, info_is_youtube: bool) -> str:
        label_source = TrackLabelHeuristics.split_audio_track_label_text(
            TrackLabelHeuristics.audio_track_display_name(fmt)
        )
        if not label_source:
            raw_track_id = TrackLabelHeuristics.audio_track_raw_id(fmt)
            if raw_track_id and info_is_youtube:
                label_source = raw_track_id

        if not label_source and info_is_youtube:
            for token in TrackLabelHeuristics.audio_track_note_tokens(fmt, include_format=False):
                if not TrackLabelHeuristics.looks_like_media_descriptor(token):
                    label_source = token
                    break
            if not label_source and TrackLabelHeuristics.audio_track_note_tokens(fmt, include_format=False):
                label_source = TrackLabelHeuristics.youtube_audio_role_label(fmt)

        if not label_source and not info_is_youtube:
            for key in ("language", "lang", "audio_lang"):
                text = TrackLabelHeuristics.split_audio_track_label_text(fmt.get(key))
                if text and not TrackLabelHeuristics.looks_like_media_descriptor(text):
                    label_source = text
                    break

        if not label_source and not info_is_youtube:
            for key in ("format_note", "format"):
                text = TrackLabelHeuristics.split_audio_track_label_text(fmt.get(key))
                if text and not TrackLabelHeuristics.looks_like_media_descriptor(text):
                    label_source = text
                    break

        if (
            label_source
            and TrackLabelHeuristics.audio_track_is_default(fmt)
            and "(default)" not in label_source.lower()
        ):
            label_source = f"{label_source} (default)"
        return label_source

    @staticmethod
    def audio_track_signature_blob(
        *,
        audio_track_key: str,
        lang_code: str,
        label_source: str,
        is_default: bool,
        language_preference: int,
    ) -> str:
        payload = {
            "audio_track_key": str(audio_track_key or ""),
            "is_default": bool(is_default),
            "lang_code": str(lang_code or ""),
            "language_preference": int(language_preference or 0),
            "label_source": str(label_source or ""),
        }
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def make_audio_track_id(*, signature_blob: str) -> str:
        return hashlib.sha1(signature_blob.encode("utf-8", errors="ignore")).hexdigest()[:12]

    @staticmethod
    def candidate_probe_client(candidate: dict[str, Any] | None) -> str:
        if not isinstance(candidate, dict):
            return "default"
        return YtdlpGateway.normalize_probe_client(candidate.get("probe_client"))

    @staticmethod
    def track_display_label(track: dict[str, Any] | None) -> str:
        if not isinstance(track, dict):
            return ""
        label = str(track.get("label") or "").strip()
        lang_code = str(track.get("lang_code") or "").strip()
        prefix = f"{lang_code} - "
        if lang_code and label.startswith(prefix):
            return label[len(prefix):].strip()
        return label

    @staticmethod
    def track_role(track: dict[str, Any] | None) -> str:
        text = TrackLabelHeuristics.track_display_label(track).lower()
        if "descriptive" in text:
            return "descriptive"
        if "original" in text:
            return "original"
        if "(default)" in text:
            return "default"
        return ""

    @staticmethod
    def track_is_default(track: dict[str, Any] | None) -> bool:
        if not isinstance(track, dict):
            return False
        if "(default)" in TrackLabelHeuristics.track_display_label(track).lower():
            return True
        try:
            return int(track.get("language_preference") or 0) == 5
        except (TypeError, ValueError):
            return False

    @staticmethod
    def track_canonical_label(track: dict[str, Any] | None) -> str:
        label = TrackLabelHeuristics.track_display_label(track)
        if not label:
            return ""
        label = re.sub(r"\s*\(default\)\s*$", "", label, flags=re.IGNORECASE).strip()
        role = TrackLabelHeuristics.track_role(track)
        if role in {"original", "descriptive", "default"}:
            return ""
        return label

    @staticmethod
    def audio_track_merge_signature_blob(track: dict[str, Any]) -> str:
        payload = {
            "canonical_label": TrackLabelHeuristics.track_canonical_label(track),
            "is_default": TrackLabelHeuristics.track_is_default(track),
            "lang_code": str(track.get("lang_code") or "").strip(),
            "role": TrackLabelHeuristics.track_role(track),
        }
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

class TrackInventory:
    """Collect and query stable audio-track inventories from extractor metadata."""

    @staticmethod
    def has_audio(fmt: dict[str, Any]) -> bool:
        return fmt.get("acodec") not in (None, "none")

    @staticmethod
    def has_video(fmt: dict[str, Any]) -> bool:
        return fmt.get("vcodec") not in (None, "none")

    @staticmethod
    def formats(info: dict[str, Any] | None) -> list[dict[str, Any]]:
        raw = [] if not isinstance(info, dict) else list(info.get("formats") or [])
        return [fmt for fmt in raw if isinstance(fmt, dict)]

    @staticmethod
    def downloadable_media_counts(info: dict[str, Any] | None) -> dict[str, int]:
        counts = {
            "media_format_count": 0,
            "audio_only_format_count": 0,
            "video_only_format_count": 0,
            "combined_format_count": 0,
            "image_only_format_count": 0,
        }
        for fmt in TrackInventory.formats(info):
            has_audio = TrackInventory.has_audio(fmt)
            has_video = TrackInventory.has_video(fmt)
            if has_audio and has_video:
                counts["media_format_count"] += 1
                counts["combined_format_count"] += 1
                continue
            if has_audio:
                counts["media_format_count"] += 1
                counts["audio_only_format_count"] += 1
                continue
            if has_video:
                counts["media_format_count"] += 1
                counts["video_only_format_count"] += 1
                continue
            format_note = str(fmt.get("format_note") or "").strip().lower()
            resolution = str(fmt.get("resolution") or "").strip().lower()
            if "image" in format_note or resolution == "storyboard":
                counts["image_only_format_count"] += 1
        return counts

    @staticmethod
    def has_downloadable_media(info: dict[str, Any] | None) -> bool:
        counts = TrackInventory.downloadable_media_counts(info)
        return bool(counts.get("media_format_count"))

    @staticmethod
    def trusted_audio_track_identity(
        fmt: dict[str, Any],
        *,
        require_audio_only: bool,
    ) -> dict[str, Any] | None:
        if not isinstance(fmt, dict) or not TrackInventory.has_audio(fmt):
            return None
        if require_audio_only and TrackInventory.has_video(fmt):
            return None

        raw_track_id = TrackLabelHeuristics.audio_track_raw_id(fmt)
        display_name = TrackLabelHeuristics.audio_track_display_name(fmt)
        is_default = TrackLabelHeuristics.audio_track_is_default(fmt)
        lang_code = TrackLabelHeuristics.audio_track_language_code(fmt)
        language_preference = TrackLabelHeuristics.audio_track_language_preference(fmt)
        label_source = TrackLabelHeuristics.audio_track_label_source(fmt, info_is_youtube=True)
        if not lang_code or not (raw_track_id or display_name or label_source):
            return None

        label_source = label_source or raw_track_id or lang_code
        if is_default and "(default)" not in label_source.lower():
            label_source = f"{label_source} (default)"
        signature_blob = TrackLabelHeuristics.audio_track_signature_blob(
            audio_track_key=raw_track_id,
            lang_code=lang_code,
            label_source=label_source,
            is_default=is_default,
            language_preference=language_preference,
        )
        return {
            "signature_blob": signature_blob,
            "track_id": TrackLabelHeuristics.make_audio_track_id(signature_blob=signature_blob),
            "track_key": raw_track_id,
            "lang_code": lang_code,
            "label_source": label_source,
            "is_default": is_default,
            "language_preference": language_preference,
        }

    @staticmethod
    def fallback_audio_track_identity(
        fmt: dict[str, Any],
        *,
        require_audio_only: bool,
    ) -> dict[str, Any] | None:
        if not isinstance(fmt, dict) or not TrackInventory.has_audio(fmt):
            return None
        if require_audio_only and TrackInventory.has_video(fmt):
            return None

        lang_code = TrackLabelHeuristics.audio_track_language_code(fmt)
        if not lang_code:
            return None

        label_source = TrackLabelHeuristics.audio_track_label_source(fmt, info_is_youtube=False) or lang_code
        if TrackLabelHeuristics.looks_like_media_descriptor(label_source):
            return None

        language_preference = TrackLabelHeuristics.audio_track_language_preference(fmt)
        signature_blob = TrackLabelHeuristics.audio_track_signature_blob(
            audio_track_key="",
            lang_code=lang_code,
            label_source=label_source,
            is_default=False,
            language_preference=language_preference,
        )
        return {
            "signature_blob": signature_blob,
            "track_id": TrackLabelHeuristics.make_audio_track_id(signature_blob=signature_blob),
            "track_key": "",
            "lang_code": lang_code,
            "label_source": label_source,
            "is_default": False,
            "language_preference": language_preference,
        }

    @staticmethod
    def coerce_track_metric(value: Any) -> int | None:
        try:
            metric = int(round(float(value)))
        except (TypeError, ValueError):
            return None
        return metric if metric > 0 else None

    @staticmethod
    def audio_track_candidate(
        fmt: dict[str, Any],
        *,
        probe_client: str = "default",
    ) -> dict[str, Any] | None:
        if not isinstance(fmt, dict) or not TrackInventory.has_audio(fmt):
            return None

        format_id = str(fmt.get("format_id") or "").strip()
        if not format_id:
            return None

        return {
            "format_id": format_id,
            "ext": str(fmt.get("ext") or "").strip().lower(),
            "abr": TrackInventory.coerce_track_metric(fmt.get("abr")),
            "tbr": TrackInventory.coerce_track_metric(fmt.get("tbr")),
            "height": TrackInventory.coerce_track_metric(fmt.get("height")),
            "has_video": bool(TrackInventory.has_video(fmt)),
            "has_audio": True,
            "probe_client": YtdlpGateway.normalize_probe_client(probe_client),
        }

    @staticmethod
    def audio_track_label(*, lang_code: str, label_source: str, is_default: bool = False) -> str:
        lang = str(lang_code or "").strip()
        source = str(label_source or "").strip()

        if source:
            hinted_lang = normalize_lang_code(source, drop_region=False) or ""
            if hinted_lang and hinted_lang == lang:
                source = ""
            elif lang and source.lower() == lang.lower():
                source = ""

        if source and is_default and "(default)" not in source.lower():
            source = f"{source} (default)"

        if lang and source:
            return f"{lang} - {source}"
        return lang or source or "Unknown"

    @staticmethod
    def audio_track_sort_key(track: dict[str, Any]) -> tuple[Any, ...]:
        lang = str(track.get("lang_code") or "")
        label = str(track.get("label") or "")
        return (
            -int(track.get("language_preference") or 0),
            0 if lang else 1,
            lang,
            label.lower(),
            str(track.get("track_id") or ""),
        )

    @staticmethod
    def audio_track_candidate_sort_key(
        candidate: dict[str, Any],
        *,
        preferred_extensions: tuple[str, ...] = (),
    ) -> tuple[Any, ...]:
        preferred = {
            str(ext or "").strip().lower() for ext in preferred_extensions if str(ext or "").strip()
        }
        abr = int(candidate.get("abr") or 0)
        tbr = int(candidate.get("tbr") or 0)
        ext = str(candidate.get("ext") or "").strip().lower()
        return (
            0 if not bool(candidate.get("has_video")) else 1,
            0 if ext in preferred else 1,
            -abr,
            -tbr,
            ext,
            YtdlpGateway.probe_client_sort_key(candidate.get("probe_client")),
            str(candidate.get("format_id") or ""),
        )

    @staticmethod
    def dedupe_audio_track_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        best: dict[tuple[str, str], dict[str, Any]] = {}
        for candidate in candidates:
            format_id = str(candidate.get("format_id") or "").strip()
            if not format_id:
                continue
            key = (TrackLabelHeuristics.candidate_probe_client(candidate), format_id)
            current = best.get(key)
            if current is None:
                best[key] = dict(candidate)
                continue
            candidate_key = TrackInventory.audio_track_candidate_sort_key(candidate)
            current_key = TrackInventory.audio_track_candidate_sort_key(current)
            if candidate_key < current_key:
                best[key] = dict(candidate)
        return sorted(best.values(), key=TrackInventory.audio_track_candidate_sort_key)

    @staticmethod
    def build_audio_track_inventory(
        info: dict[str, Any],
        *,
        probe_client: str = "default",
    ) -> dict[str, Any]:
        """Build the per-client audio-track inventory used by download probing."""
        info_is_youtube = TrackLabelHeuristics.info_is_youtube(info)
        normalized_probe_client = YtdlpGateway.normalize_probe_client(probe_client)
        tracks_by_signature: dict[str, dict[str, Any]] = {}
        audio_format_count = 0
        discarded_audio_candidate_count = 0
        untrusted_audio_format_count = 0

        for fmt in TrackInventory.formats(info):
            identity = TrackInventory.trusted_audio_track_identity(fmt, require_audio_only=False)
            if identity is None and not info_is_youtube:
                identity = TrackInventory.fallback_audio_track_identity(fmt, require_audio_only=True)
            if identity is None:
                continue

            signature_blob = str(identity.get("signature_blob") or "")
            track = tracks_by_signature.setdefault(
                signature_blob,
                {
                    "track_id": str(identity.get("track_id") or ""),
                    "lang_code": str(identity.get("lang_code") or ""),
                    "language_preference": int(identity.get("language_preference") or 0),
                    "_label_source": str(identity.get("label_source") or ""),
                    "_track_key": str(identity.get("track_key") or ""),
                    "_is_default": bool(identity.get("is_default")),
                    "candidates": [],
                },
            )
            track["language_preference"] = max(
                int(track.get("language_preference") or 0),
                int(identity.get("language_preference") or 0),
            )

        tracks_by_lang: dict[str, list[str]] = {}
        for signature_blob, track in tracks_by_signature.items():
            lang_code = str(track.get("lang_code") or "")
            if lang_code:
                tracks_by_lang.setdefault(lang_code, []).append(signature_blob)

        for fmt in TrackInventory.formats(info):
            candidate = TrackInventory.audio_track_candidate(fmt, probe_client=normalized_probe_client)
            if candidate is None:
                continue
            audio_format_count += 1

            trusted_identity = TrackInventory.trusted_audio_track_identity(fmt, require_audio_only=False)
            fallback_identity = None
            if trusted_identity is None and not info_is_youtube and not TrackInventory.has_video(fmt):
                fallback_identity = TrackInventory.fallback_audio_track_identity(fmt, require_audio_only=False)

            matched = False
            for identity in (trusted_identity, fallback_identity):
                if identity is None:
                    continue
                signature_blob = str(identity.get("signature_blob") or "")
                track = tracks_by_signature.get(signature_blob)
                if track is None:
                    continue
                track["candidates"].append(candidate)
                matched = True
                break

            if not matched:
                lang_code = TrackLabelHeuristics.audio_track_language_code(fmt)
                signatures = tracks_by_lang.get(lang_code) or []
                if lang_code and len(signatures) == 1:
                    tracks_by_signature[signatures[0]]["candidates"].append(candidate)
                    matched = True

            if trusted_identity is None:
                untrusted_audio_format_count += 1
            if not matched:
                discarded_audio_candidate_count += 1

        tracks = list(tracks_by_signature.values())
        for track in tracks:
            track["candidates"] = TrackInventory.dedupe_audio_track_candidates(list(track.get("candidates") or []))
            track["label"] = TrackInventory.audio_track_label(
                lang_code=str(track.get("lang_code") or ""),
                label_source=str(track.get("_label_source") or ""),
                is_default=bool(track.get("_is_default")),
            )

        label_counts: dict[str, int] = {}
        for track in tracks:
            label = str(track.get("label") or "")
            label_counts[label] = label_counts.get(label, 0) + 1

        disambiguated_counts: dict[str, int] = {}
        for track in tracks:
            label = str(track.get("label") or "")
            if label_counts.get(label, 0) > 1:
                track_key = str(track.get("_track_key") or "").strip()
                lang_code = str(track.get("lang_code") or "")
                if track_key and f"[{track_key}]" not in label:
                    label = f"{label} [{track_key}]"
                elif lang_code and f"[{lang_code}]" not in label:
                    label = f"{label} [{lang_code}]"
                if disambiguated_counts.get(label, 0) > 0:
                    label = f"{label} #{str(track.get('track_id') or '')[:4]}"
                track["label"] = label
            normalized_label = str(track.get("label") or "")
            disambiguated_counts[normalized_label] = disambiguated_counts.get(normalized_label, 0) + 1

        normalized_tracks: list[dict[str, Any]] = []
        for track in sorted(tracks, key=TrackInventory.audio_track_sort_key):
            download_clients = TrackInventory.ordered_download_clients_for_track(track)
            normalized_tracks.append(
                {
                    "track_id": str(track.get("track_id") or ""),
                    "lang_code": str(track.get("lang_code") or ""),
                    "label": str(track.get("label") or ""),
                    "language_preference": int(track.get("language_preference") or 0),
                    "candidates": list(track.get("candidates") or []),
                    "download_clients": download_clients,
                    "downloadable": bool(download_clients),
                }
            )

        return {
            "tracks": normalized_tracks,
            "audio_format_count": audio_format_count,
            "certain_audio_track_count": len(normalized_tracks),
            "discarded_audio_candidate_count": discarded_audio_candidate_count,
            "untrusted_audio_format_count": untrusted_audio_format_count,
            "info_is_youtube": info_is_youtube,
            "probe_client": normalized_probe_client,
        }

    @staticmethod
    def collect_audio_tracks(info: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the normalized audio-track list exposed to callers."""
        inventory = TrackInventory.build_audio_track_inventory(info)
        return list(inventory.get("tracks") or [])

    @staticmethod
    def merged_track_label_priority(track: dict[str, Any], *, probe_client: str) -> tuple[int, int, int]:
        display_label = TrackLabelHeuristics.track_display_label(track)
        lang_code = str(track.get("lang_code") or "").strip().lower()
        normalized_display = normalize_lang_code(display_label, drop_region=False) or ""
        richness = (
            len(display_label)
            if display_label and normalized_display != lang_code and display_label.lower() != lang_code
            else 0
        )
        return (
            int(TrackLabelHeuristics.track_is_default(track)),
            richness,
            -YtdlpGateway.probe_client_sort_key(probe_client)[0],
        )

    @staticmethod
    def _is_partial_probe_inventory(inventory: dict[str, Any] | None) -> bool:
        payload = inventory if isinstance(inventory, dict) else {}
        return (
            int(payload.get("untrusted_audio_format_count") or 0) > 0
            or int(payload.get("discarded_audio_candidate_count") or 0) > 0
            or (
                int(payload.get("audio_format_count") or 0) > 0
                and int(payload.get("certain_audio_track_count") or 0) == 0
            )
        )

    @staticmethod
    def finalize_probe_inventory(
        *,
        inventories_by_client: dict[str, dict[str, Any]],
        attempted_clients: tuple[str, ...],
    ) -> dict[str, Any]:
        normalized_inventories: dict[str, dict[str, Any]] = {}
        for client, inventory in inventories_by_client.items():
            if not isinstance(inventory, dict):
                continue
            normalized_client = YtdlpGateway.normalize_probe_client(client)
            normalized_inventories[normalized_client] = dict(inventory)

        attempted: list[str] = [
            YtdlpGateway.normalize_probe_client(client)
            for client in attempted_clients
        ]
        for client in sorted(normalized_inventories, key=YtdlpGateway.probe_client_sort_key):
            if client not in attempted:
                attempted.append(client)

        if not normalized_inventories:
            return {
                "tracks": [],
                "audio_format_count": 0,
                "certain_audio_track_count": 0,
                "discarded_audio_candidate_count": 0,
                "untrusted_audio_format_count": 0,
                "info_is_youtube": False,
                "attempted_probe_clients": attempted,
                "successful_probe_clients": [],
                "merged_audio_track_count": 0,
                "client_track_coverage": {},
                "partial_probe_clients": [],
            }

        return TrackInventory._merge_audio_track_inventories(
            inventories_by_client=normalized_inventories,
            attempted_clients=tuple(attempted),
        )

    @staticmethod
    def _merge_audio_track_inventories(
        *,
        inventories_by_client: dict[str, dict[str, Any]],
        attempted_clients: tuple[str, ...],
    ) -> dict[str, Any]:
        merged_by_signature: dict[str, dict[str, Any]] = {}
        attempted = [YtdlpGateway.normalize_probe_client(client) for client in attempted_clients]
        successful = [client for client in attempted if isinstance(inventories_by_client.get(client), dict)]
        audio_format_count = 0
        discarded_audio_candidate_count = 0
        untrusted_audio_format_count = 0
        client_track_coverage: dict[str, int] = {}
        partial_clients: set[str] = set()
        info_is_youtube = False

        for client in successful:
            inventory = inventories_by_client.get(client) or {}
            tracks = list(inventory.get("tracks") or [])
            info_is_youtube = info_is_youtube or bool(inventory.get("info_is_youtube"))
            audio_format_count = max(audio_format_count, int(inventory.get("audio_format_count") or 0))
            discarded_audio_candidate_count = max(
                discarded_audio_candidate_count,
                int(inventory.get("discarded_audio_candidate_count") or 0),
            )
            untrusted_audio_format_count = max(
                untrusted_audio_format_count,
                int(inventory.get("untrusted_audio_format_count") or 0),
            )
            client_track_coverage[client] = len(tracks)
            if TrackInventory._is_partial_probe_inventory(inventory):
                partial_clients.add(client)

            for track in tracks:
                if not isinstance(track, dict):
                    continue
                merge_signature = TrackLabelHeuristics.audio_track_merge_signature_blob(track)
                merged = merged_by_signature.setdefault(
                    merge_signature,
                    {
                        "track_id": TrackLabelHeuristics.make_audio_track_id(signature_blob=merge_signature),
                        "lang_code": str(track.get("lang_code") or "").strip(),
                        "label": str(track.get("label") or "").strip(),
                        "language_preference": int(track.get("language_preference") or 0),
                        "candidates": [],
                        "_label_priority": TrackInventory.merged_track_label_priority(
                            track,
                            probe_client=client,
                        ),
                    },
                )
                merged["language_preference"] = max(
                    int(merged.get("language_preference") or 0),
                    int(track.get("language_preference") or 0),
                )
                merged["candidates"].extend(list(track.get("candidates") or []))
                label_priority = TrackInventory.merged_track_label_priority(track, probe_client=client)
                if label_priority > tuple(merged.get("_label_priority") or (0, 0, 0)):
                    merged["label"] = str(track.get("label") or "").strip()
                    merged["_label_priority"] = label_priority

        merged_tracks: list[dict[str, Any]] = []
        for track in merged_by_signature.values():
            merged_tracks.append(
                {
                    "track_id": str(track.get("track_id") or "").strip(),
                    "lang_code": str(track.get("lang_code") or "").strip(),
                    "label": str(track.get("label") or "").strip(),
                    "language_preference": int(track.get("language_preference") or 0),
                    "candidates": TrackInventory.dedupe_audio_track_candidates(list(track.get("candidates") or [])),
                }
            )

        merged_tracks = sorted(merged_tracks, key=TrackInventory.audio_track_sort_key)
        return {
            "tracks": merged_tracks,
            "audio_format_count": audio_format_count,
            "certain_audio_track_count": len(merged_tracks),
            "discarded_audio_candidate_count": discarded_audio_candidate_count,
            "untrusted_audio_format_count": untrusted_audio_format_count,
            "info_is_youtube": info_is_youtube,
            "attempted_probe_clients": attempted,
            "successful_probe_clients": successful,
            "merged_audio_track_count": len(merged_tracks),
            "client_track_coverage": client_track_coverage,
            "partial_probe_clients": sorted(partial_clients, key=YtdlpGateway.probe_client_sort_key),
        }

    @staticmethod
    def build_probe_variant_payload(
        info: dict[str, Any],
        *,
        probe_client: str,
        inventory: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "_probe_client": YtdlpGateway.normalize_probe_client(probe_client),
            "formats": list(info.get("formats") or []),
            "inventory": {
                "tracks": list(inventory.get("tracks") or []),
                "audio_format_count": int(inventory.get("audio_format_count") or 0),
                "certain_audio_track_count": int(inventory.get("certain_audio_track_count") or 0),
                "discarded_audio_candidate_count": int(inventory.get("discarded_audio_candidate_count") or 0),
                "untrusted_audio_format_count": int(inventory.get("untrusted_audio_format_count") or 0),
            },
        }

    @staticmethod
    def probe_variants_from_meta(meta: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        if not isinstance(meta, dict):
            return {}
        raw = meta.get("_probe_variants")
        if not isinstance(raw, dict):
            return {}

        out: dict[str, dict[str, Any]] = {}
        for key, value in raw.items():
            client = YtdlpGateway.normalize_probe_client(key)
            if isinstance(value, dict):
                out[client] = dict(value)
        return out

    @staticmethod
    def info_for_probe_client(meta: dict[str, Any] | None, probe_client: str) -> dict[str, Any] | None:
        normalized_probe_client = YtdlpGateway.normalize_probe_client(probe_client)
        variants = TrackInventory.probe_variants_from_meta(meta)
        payload = variants.get(normalized_probe_client)
        if normalized_probe_client == "default" and isinstance(meta, dict) and not payload:
            return dict(meta)
        if isinstance(payload, dict):
            info = dict(meta or {}) if isinstance(meta, dict) else {}
            info["formats"] = list(payload.get("formats") or [])
            return info
        return None

    @staticmethod
    def track_for_probe_client(track: dict[str, Any] | None, probe_client: str) -> dict[str, Any] | None:
        if not isinstance(track, dict):
            return None
        normalized_probe_client = YtdlpGateway.normalize_probe_client(probe_client)
        candidates = [
            dict(candidate)
            for candidate in list(track.get("candidates") or [])
            if TrackLabelHeuristics.candidate_probe_client(candidate) == normalized_probe_client
        ]
        if not candidates:
            return None
        return {
            "track_id": str(track.get("track_id") or "").strip(),
            "lang_code": str(track.get("lang_code") or "").strip(),
            "label": str(track.get("label") or "").strip(),
            "language_preference": int(track.get("language_preference") or 0),
            "candidates": TrackInventory.dedupe_audio_track_candidates(candidates),
        }

    @staticmethod
    def ordered_probe_clients_for_track(track: dict[str, Any] | None) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for client in DownloadPolicy.youtube_enhanced_probe_clients():
            if TrackInventory.track_for_probe_client(track, client) is None:
                continue
            seen.add(client)
            ordered.append(client)
        for candidate in list((track or {}).get("candidates") or []):
            client = TrackLabelHeuristics.candidate_probe_client(candidate)
            if client in seen:
                continue
            seen.add(client)
            ordered.append(client)
        return ordered

    @staticmethod
    def ordered_download_clients_for_track(track: dict[str, Any] | None) -> list[str]:
        if not isinstance(track, dict):
            return []
        return TrackInventory.ordered_probe_clients_for_track(track)

    @staticmethod
    def make_probe_diagnostics(
        *,
        info: dict[str, Any] | None,
        audio_tracks: list[dict[str, Any]],
        inventory: dict[str, Any],
        js_runtime_fallback: bool,
        js_runtime_detail: str,
        cookie_runtime_fallback: bool,
        cookie_runtime_failures: list[dict[str, Any]],
        authentication_required: bool,
        authentication_detail: str,
        no_downloadable_formats: bool,
        no_downloadable_formats_detail: str,
        extended_access_required: bool,
        extended_access_required_detail: str,
        extractor_access_limited: bool,
        extractor_access_limited_detail: str,
        browser_cookie_requested: bool,
        enhanced_mode: bool,
        extractor_access_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        warnings: list[str] = []
        errors: list[str] = []
        details: dict[str, Any] = {}

        audio_format_count = int(inventory.get("audio_format_count") or 0)
        certain_audio_track_count = int(inventory.get("certain_audio_track_count") or 0)
        discarded_audio_candidate_count = int(inventory.get("discarded_audio_candidate_count") or 0)
        untrusted_audio_format_count = int(inventory.get("untrusted_audio_format_count") or 0)
        info_is_youtube = bool(inventory.get("info_is_youtube"))
        attempted_probe_clients = list(inventory.get("attempted_probe_clients") or [])
        successful_probe_clients = list(inventory.get("successful_probe_clients") or [])
        merged_audio_track_count = int(inventory.get("merged_audio_track_count") or len(audio_tracks))
        client_track_coverage = dict(inventory.get("client_track_coverage") or {})
        partial_probe_clients = list(inventory.get("partial_probe_clients") or [])

        if js_runtime_fallback:
            warnings.append("runtime_fallback")
            if js_runtime_detail:
                details["runtime_fallback_detail"] = js_runtime_detail

        if browser_cookie_requested and cookie_runtime_fallback:
            warnings.append("browser_cookies_unavailable")
            if cookie_runtime_failures:
                details["cookie_browser_failures"] = list(cookie_runtime_failures)

        if authentication_required:
            warnings.append("authentication_required")
            if authentication_detail:
                details["authentication_detail"] = authentication_detail

        media_counts = TrackInventory.downloadable_media_counts(info)
        no_public_formats = not bool(media_counts.get("media_format_count")) or no_downloadable_formats
        if extended_access_required:
            warnings.append("extended_access_required")
            if extended_access_required_detail:
                details["extended_access_required_detail"] = extended_access_required_detail
        if extractor_access_limited:
            warnings.append("extractor_access_limited")
            if extractor_access_limited_detail:
                details["extractor_access_limited_detail"] = extractor_access_limited_detail
        if no_public_formats:
            warnings.append("no_public_formats")
        if enhanced_mode and not bool(media_counts.get("media_format_count")):
            warnings.append("media_unavailable")
        if enhanced_mode and no_downloadable_formats:
            warnings.append("no_downloadable_formats")
        if no_downloadable_formats_detail:
            details["no_downloadable_formats_detail"] = no_downloadable_formats_detail

        if enhanced_mode:
            if info_is_youtube and partial_probe_clients:
                warnings.append("audio_metadata_partial")
            elif info_is_youtube and untrusted_audio_format_count > 0:
                warnings.append("audio_metadata_partial")

            if info_is_youtube and audio_format_count > 0 and certain_audio_track_count == 0:
                warnings.append("audio_tracks_incomplete")

            if any(not bool(track.get("downloadable")) for track in audio_tracks):
                warnings.append("audio_tracks_probe_only")
        elif info_is_youtube and (
            untrusted_audio_format_count > 0
            or (audio_format_count > 0 and certain_audio_track_count == 0)
        ):
            warnings.append("partial_metadata")

        decision_payload = dict(extractor_access_decision or {})
        decision_state = str(decision_payload.get("state") or "").strip()
        notable_decision = (
            DownloadPolicy.is_limited_extractor_access_decision(decision_state)
            or DownloadPolicy.is_unavailable_extractor_access_state(decision_state)
        )
        if notable_decision:
            details.setdefault("extractor_access_state", decision_state)
            details.setdefault("extractor_access_action", str(decision_payload.get("action") or ""))
            details.setdefault("extractor_access_scope", str(decision_payload.get("scope") or ""))
            details.setdefault("extractor_access_mode", str(decision_payload.get("access_mode") or ""))
            if str(decision_payload.get("detail") or "").strip():
                details.setdefault("extractor_access_detail", str(decision_payload.get("detail") or "").strip())
            details.setdefault("extractor_access_decision", decision_payload)

        if not warnings and not errors and not notable_decision:
            return {}

        details.setdefault("audio_format_count", audio_format_count)
        details.setdefault("certain_audio_track_count", certain_audio_track_count)
        details.setdefault("audio_track_count", len(audio_tracks))
        details.setdefault("merged_audio_track_count", merged_audio_track_count)
        details.setdefault("discarded_audio_candidate_count", discarded_audio_candidate_count)
        details.setdefault("untrusted_audio_format_count", untrusted_audio_format_count)
        details.setdefault("media_format_count", int(media_counts.get("media_format_count") or 0))
        details.setdefault("audio_only_format_count", int(media_counts.get("audio_only_format_count") or 0))
        details.setdefault("video_only_format_count", int(media_counts.get("video_only_format_count") or 0))
        details.setdefault("combined_format_count", int(media_counts.get("combined_format_count") or 0))
        details.setdefault("image_only_format_count", int(media_counts.get("image_only_format_count") or 0))
        if enhanced_mode and attempted_probe_clients:
            details.setdefault("attempted_probe_clients", attempted_probe_clients)
        if enhanced_mode and successful_probe_clients:
            details.setdefault("successful_probe_clients", successful_probe_clients)
        if enhanced_mode and client_track_coverage:
            details.setdefault("client_track_coverage", client_track_coverage)
        if enhanced_mode and partial_probe_clients:
            details.setdefault("partial_probe_clients", partial_probe_clients)
        return {
            "warnings": warnings,
            "errors": errors,
            "details": details,
        }

    @staticmethod
    def available_video_heights(
        info: dict[str, Any] | None,
        *,
        min_h: int | None = None,
        max_h: int | None = None,
    ) -> list[int]:
        heights: set[int] = set()
        for fmt in TrackInventory.formats(info):
            if not TrackInventory.has_video(fmt):
                continue
            try:
                height = int(fmt.get("height") or 0)
            except (TypeError, ValueError):
                continue
            if height <= 0:
                continue
            if isinstance(min_h, int) and min_h > 0 and height < min_h:
                continue
            if isinstance(max_h, int) and 0 < max_h < height:
                continue
            heights.add(height)
        return sorted(heights, reverse=True)

    @staticmethod
    def available_audio_bitrates(info: dict[str, Any] | None) -> list[int]:
        bitrates: set[int] = set()
        for fmt in TrackInventory.formats(info):
            if not TrackInventory.has_audio(fmt):
                continue
            raw = fmt.get("abr", fmt.get("tbr"))
            try:
                bitrate = int(round(float(raw or 0)))
            except (TypeError, ValueError):
                continue
            if bitrate > 0:
                bitrates.add(bitrate)
        return sorted(bitrates, reverse=True)

    @staticmethod
    def find_audio_track(info: dict[str, Any] | None, audio_track_id: str) -> dict[str, Any] | None:
        wanted = str(audio_track_id or "").strip()
        if not wanted:
            return None
        if isinstance(info, dict):
            for track in list(info.get("audio_tracks") or []):
                if str((track or {}).get("track_id") or "").strip() == wanted:
                    return dict(track)
        for track in TrackInventory.collect_audio_tracks(info or {}):
            if str(track.get("track_id") or "").strip() == wanted:
                return track
        return None
