# app/model/transcription/writer.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def clean_text(text: str) -> str:
    """Light cleanup for ASR output (newlines and whitespace)."""
    t = text.replace("\r\n", "\n")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _format_ts_srt(seconds: float) -> str:
    """Format seconds into SRT timestamp HH:MM:SS,mmm."""
    if seconds < 0:
        seconds = 0.0
    ms_total = int(round(seconds * 1000))
    s, ms = divmod(ms_total, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_ts_plain(seconds: float) -> str:
    """Format seconds into HH:MM:SS for plain timestamped text."""
    if seconds < 0:
        seconds = 0.0
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class TextPostprocessor:
    """Helpers for post-processing ASR output."""

    @staticmethod
    def clean(text: str) -> str:
        """Clean arbitrary text."""
        return clean_text(text)

    @staticmethod
    def plain_from_result(result: Any) -> str:
        """Extract plain text from pipeline result and clean it."""
        if isinstance(result, dict):
            text = result.get("text", "")
        else:
            text = result
        return clean_text(str(text))

    @staticmethod
    def segments_from_result(result: Any) -> list[dict[str, Any]]:
        """Build a normalized list of segments from pipeline result."""
        raw_segments: list[Any] = []

        if isinstance(result, dict):
            if isinstance(result.get("chunks"), list):
                raw_segments = result["chunks"]
            elif isinstance(result.get("segments"), list):
                raw_segments = result["segments"]

        segments: list[dict[str, Any]] = []
        for ch in raw_segments:
            if not isinstance(ch, dict):
                continue

            text = clean_text(str(ch.get("text", "")))
            if not text:
                continue

            ts = ch.get("timestamp")
            if isinstance(ts, (list, tuple)) and len(ts) == 2:
                start_value, end_value = ts
            else:
                start_value = ch.get("start")
                end_value = ch.get("end")

            try:
                start_f = float(start_value) if start_value is not None else 0.0
            except (TypeError, ValueError):
                start_f = 0.0
            try:
                end_f = float(end_value) if end_value is not None else start_f
            except (TypeError, ValueError):
                end_f = start_f

            if end_f < start_f:
                end_f = start_f

            segments.append({"start": start_f, "end": end_f, "text": text})

        if segments:
            return segments

        text = TextPostprocessor.plain_from_result(result)
        if not text:
            return []
        return [{"start": 0.0, "end": 0.0, "text": text}]

    @staticmethod
    def to_plain(segments: list[dict[str, Any]]) -> str:
        """Join segments into plain text (one segment per line)."""
        lines: list[str] = []
        for seg in segments:
            text = clean_text(str(seg.get("text", "")))
            if text:
                lines.append(text)
        return "\n".join(lines).strip()

    @staticmethod
    def to_srt(segments: list[dict[str, Any]]) -> str:
        """Render segments as SRT subtitles."""
        lines: list[str] = []
        idx = 1

        for seg in segments:
            text = clean_text(str(seg.get("text", "")))
            if not text:
                continue

            start = float(seg.get("start", 0.0) or 0.0)
            end = float(seg.get("end", start) or start)
            if end <= start:
                end = start + 0.5

            lines.append(str(idx))
            lines.append(f"{_format_ts_srt(start)} --> {_format_ts_srt(end)}")
            lines.append(text)
            lines.append("")
            idx += 1

        return "\n".join(lines).rstrip()

    @staticmethod
    def to_timestamped_plain(segments: list[dict[str, Any]]) -> str:
        """Render segments as plain text with timestamps."""
        lines: list[str] = []
        for seg in segments:
            text = clean_text(str(seg.get("text", "")))
            if not text:
                continue
            start = float(seg.get("start", 0.0) or 0.0)
            ts = _format_ts_plain(start)
            lines.append(f"{ts} {text}")
        return "\n".join(lines).rstrip()


class TranscriptWriter:
    """Shared transcript rendering and saving helpers for batch and live flows."""

    @staticmethod
    def offset_segments(segments: list[dict[str, Any]], *, offset_s: float) -> list[dict[str, Any]]:
        """Return normalized segments shifted by a constant offset."""
        out: list[dict[str, Any]] = []
        for seg in list(segments or []):
            try:
                start = float(seg.get("start", 0.0) or 0.0) + float(offset_s)
            except (TypeError, ValueError):
                start = float(offset_s)
            try:
                end = float(seg.get("end", start) or start) + float(offset_s)
            except (TypeError, ValueError):
                end = start
            text = clean_text(str(seg.get("text") or ""))
            if not text:
                continue
            out.append({"start": start, "end": max(start, end), "text": text})
        return out

    @staticmethod
    def render_output(
        *,
        merged_text: str,
        translated_text: str,
        translated_segments: list[dict[str, Any]] | None,
        segments: list[dict[str, Any]],
        mode: dict[str, Any],
    ) -> str:
        """Render a single transcript payload for a selected output mode."""
        out_ext = str(mode.get("ext", "txt") or "txt").strip().lower().lstrip(".") or "txt"
        timestamps_output = bool(mode.get("timestamps", False))
        if out_ext not in ("txt", "srt", "sub"):
            out_ext = "txt"

        preferred_segments = list(translated_segments or []) or list(segments or [])
        translated_clean = clean_text(str(translated_text or ""))
        merged_clean = clean_text(str(merged_text or ""))

        if out_ext == "srt":
            return TextPostprocessor.to_srt(preferred_segments)
        if out_ext == "txt" and timestamps_output:
            return TextPostprocessor.to_timestamped_plain(preferred_segments)
        if translated_clean:
            return translated_clean
        if merged_clean:
            return merged_clean
        return TextPostprocessor.to_plain(preferred_segments)

    @staticmethod
    def write_mode_outputs(
        *,
        out_dir: Path,
        output_mode_ids: list[str],
        mode_resolver,
        filename_resolver,
        unique_path_resolver,
        merged_text: str,
        translated_text: str,
        translated_segments: list[dict[str, Any]] | None,
        segments: list[dict[str, Any]],
    ) -> list[Path]:
        """Render and write all requested transcript outputs."""
        written_paths: list[Path] = []
        out_dir.mkdir(parents=True, exist_ok=True)

        for mode_id in output_mode_ids:
            mode = dict(mode_resolver(str(mode_id)) or {})
            out_text = TranscriptWriter.render_output(
                merged_text=merged_text,
                translated_text=translated_text,
                translated_segments=translated_segments,
                segments=segments,
                mode=mode,
            )
            out_path = unique_path_resolver(out_dir / str(filename_resolver(str(mode_id))))
            out_path.write_text(out_text, encoding="utf-8")
            written_paths.append(out_path)

        return written_paths

    @staticmethod
    def save_live_transcript(
        *,
        target_path: str,
        source_text: str,
        target_text: str,
        write_source_companion: bool,
    ) -> list[Path]:
        """Save live transcript to the selected path and optional source companion file."""
        main_path = Path(str(target_path))
        source_clean = clean_text(str(source_text or ""))
        target_clean = clean_text(str(target_text or ""))
        main_content = target_clean or source_clean
        if not main_content:
            return []

        main_path.write_text(main_content, encoding="utf-8")
        written = [main_path]

        if write_source_companion and source_clean:
            if main_path.suffix:
                source_path = main_path.with_name(main_path.stem + "_og" + main_path.suffix)
            else:
                source_path = Path(str(main_path) + "_og")
            source_path.write_text(source_clean, encoding="utf-8")
            written.append(source_path)

        return written
