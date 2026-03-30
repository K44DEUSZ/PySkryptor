# app/model/download/artifacts.py
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.model.core.config.config import AppConfig
from app.model.core.utils.string_utils import sanitize_filename
from app.model.download.policy import DownloadPolicy
from app.model.download.domain import DownloadError


class DownloadArtifactManager:
    """Manage staging directories and final artifact promotion."""

    @staticmethod
    def create_download_stage(*, stem: str) -> Path:
        root = AppConfig.PATHS.DOWNLOADS_TMP_DIR
        root.mkdir(parents=True, exist_ok=True)
        prefix = f"{sanitize_filename(stem) or DownloadPolicy.DOWNLOAD_DEFAULT_STEM}_"
        return Path(tempfile.mkdtemp(prefix=prefix, dir=str(root)))

    @staticmethod
    def build_stage_outtmpl(*, stage_dir: Path, stem: str) -> str:
        safe_stem = sanitize_filename(stem) or DownloadPolicy.DOWNLOAD_DEFAULT_STEM
        return str(stage_dir / f"{safe_stem}.%(ext)s")

    @staticmethod
    def normalize_ext(value: Any) -> str:
        return str(value or "").strip().lower().lstrip(".")

    @staticmethod
    def is_partial_artifact(path: Path) -> bool:
        name_lower = path.name.lower()
        if name_lower.endswith((".part", ".ytdl", ".temp")):
            return True
        if ".part-" in name_lower or name_lower.endswith(".frag"):
            return True
        return False

    @staticmethod
    def stage_files(stage_dir: Path) -> list[Path]:
        try:
            matches = [
                path
                for path in stage_dir.iterdir()
                if path.is_file() and not DownloadArtifactManager.is_partial_artifact(path)
            ]
        except OSError:
            return []

        return sorted(
            matches,
            key=lambda path: (
                path.stat().st_mtime_ns if path.exists() else 0,
                path.stat().st_size if path.exists() else 0,
            ),
            reverse=True,
        )

    @staticmethod
    def candidate_paths_from_info(info: dict[str, Any]) -> list[Path]:
        candidates: list[Path] = []

        def _add(value: Any) -> None:
            if not value:
                return
            try:
                path = Path(value)
            except (TypeError, ValueError, OSError):
                return
            if path.exists() and path.is_file() and not DownloadArtifactManager.is_partial_artifact(path):
                candidates.append(path)

        _add(info.get("filepath"))
        _add(info.get("_filename"))
        return list(dict.fromkeys(candidates))

    @staticmethod
    def requested_component_paths(info: dict[str, Any], stage_dir: Path) -> list[Path]:
        paths: list[Path] = []
        requested_downloads = info.get("requested_downloads") or []
        if not isinstance(requested_downloads, list):
            return paths

        for item in requested_downloads:
            if not isinstance(item, dict):
                continue
            for key in ("filepath", "_filename"):
                value = item.get(key)
                if not value:
                    continue
                try:
                    path = Path(value)
                except (TypeError, ValueError, OSError):
                    continue
                if (
                    stage_dir in path.parents
                    and path.exists()
                    and path.is_file()
                    and not DownloadArtifactManager.is_partial_artifact(path)
                ):
                    paths.append(path)

        return list(dict.fromkeys(paths))

    @staticmethod
    def select_matching_ext(paths: list[Path], ext_l: str) -> Path | None:
        if not ext_l:
            return None
        for path in paths:
            if DownloadArtifactManager.normalize_ext(path.suffix) == ext_l:
                return path
        return None

    @staticmethod
    def resolve_stage_artifact(
        *,
        info: dict[str, Any],
        stage_dir: Path,
        stem: str,
        requested_ext: str,
        artifact_policy: str,
    ) -> Path | None:
        requested_ext_l = DownloadArtifactManager.normalize_ext(requested_ext)
        info_ext_l = DownloadArtifactManager.normalize_ext(info.get("ext"))
        safe_stem = sanitize_filename(stem) or DownloadPolicy.DOWNLOAD_DEFAULT_STEM

        stage_files = DownloadArtifactManager.stage_files(stage_dir)
        if not stage_files:
            return None

        component_paths = set(DownloadArtifactManager.requested_component_paths(info, stage_dir))
        info_candidates = [
            path
            for path in DownloadArtifactManager.candidate_paths_from_info(info)
            if stage_dir in path.parents and path in stage_files
        ]
        exact_stem = [path for path in stage_files if path.stem == safe_stem]

        def _prefer_non_component(paths: list[Path]) -> list[Path]:
            preferred = [path for path in paths if path not in component_paths]
            return preferred or list(paths)

        info_preferred = _prefer_non_component(info_candidates)
        exact_preferred = _prefer_non_component(exact_stem)
        stage_preferred = _prefer_non_component(stage_files)

        for pool in (info_preferred, exact_preferred, stage_preferred):
            pick = DownloadArtifactManager.select_matching_ext(pool, requested_ext_l)
            if pick is not None:
                return pick

        for pool in (info_preferred, exact_preferred, stage_preferred):
            pick = DownloadArtifactManager.select_matching_ext(pool, info_ext_l)
            if pick is not None:
                return pick

        if artifact_policy == DownloadPolicy.DOWNLOAD_ARTIFACT_POLICY_WORK_INPUT:
            if len(info_preferred) == 1:
                return info_preferred[0]
            if len(exact_preferred) == 1:
                return exact_preferred[0]
            if len(stage_preferred) == 1:
                return stage_preferred[0]

        if len(info_candidates) == 1:
            return info_candidates[0]
        if len(exact_stem) == 1:
            return exact_stem[0]
        if len(stage_files) == 1:
            return stage_files[0]
        return None

    @staticmethod
    def unique_destination_path(dst: Path) -> Path:
        if not dst.exists():
            return dst

        stem = dst.stem
        suffix = dst.suffix
        parent = dst.parent
        idx = 2
        while True:
            candidate = parent / f"{stem} ({idx}){suffix}"
            if not candidate.exists():
                return candidate
            idx += 1

    @staticmethod
    def promote_stage_artifact(
        *,
        artifact: Path,
        final_dir: Path,
        stem: str,
        requested_ext: str,
    ) -> Path:
        final_dir.mkdir(parents=True, exist_ok=True)

        requested_ext_l = DownloadArtifactManager.normalize_ext(requested_ext)
        artifact_ext_l = DownloadArtifactManager.normalize_ext(artifact.suffix)
        if requested_ext_l and artifact_ext_l and requested_ext_l != artifact_ext_l:
            raise DownloadError(
                "error.down.download_failed",
                detail=f"staged artifact ext mismatch: expected {requested_ext_l}, got {artifact_ext_l}",
            )

        suffix = artifact.suffix or (f".{requested_ext_l}" if requested_ext_l else "")
        dst = final_dir / f"{sanitize_filename(stem) or artifact.stem}{suffix}"
        dst = DownloadArtifactManager.unique_destination_path(dst)
        shutil.move(str(artifact), str(dst))
        return dst

    @staticmethod
    def cleanup_stage_dir(stage_dir: Path | None) -> None:
        if not stage_dir:
            return
        shutil.rmtree(stage_dir, ignore_errors=True)
