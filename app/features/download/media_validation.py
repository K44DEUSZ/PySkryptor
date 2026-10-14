# app/features/download/media_validation.py
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from babel.core import get_global

from app.core.config.config import AppConfig
from app.core.domain.errors import AppError, OperationCancelled
from app.core.infrastructure.command_runner import CommandRunner
from app.core.runtime.ffmpeg import resolve_ffmpeg_tool
from app.features.download.domain import DownloadError


def run_media_command(arguments: list[str], *, cancel_check: Callable[[], bool] | None = None) -> str:
  """Run a bounded, cancellable media operation and translate its errors."""
  try:
    return CommandRunner.run(arguments, cancel_check=cancel_check, timeout_s=120).stdout
  except AppError as error:
    if error.key == 'error.cancelled':
      raise OperationCancelled() from error
    raise DownloadError('error.download.media_validation_failed') from error


def read_media_streams(source: str | Path, *, cancel_check: Callable[[], bool] | None = None) -> dict[str, Any]:
  """Inspect actual container and streams instead of trusting a filename."""
  arguments = [resolve_ffmpeg_tool(AppConfig, 'ffprobe'), '-v', 'error']
  if str(source).startswith(('http://', 'https://')):
    arguments += ['-rw_timeout', str(AppConfig.network_http_timeout_s() * 1000000)]
  arguments += ['-show_format', '-show_streams', '-of', 'json', str(source)]
  try:
    result = json.loads(run_media_command(arguments, cancel_check=cancel_check))
    if not isinstance(result, dict) or not result.get('streams'):
      raise ValueError('No media streams')
    return result
  except (ValueError, OSError) as error:
    raise DownloadError('error.download.media_validation_failed') from error


def validate_media(
  path: Path, *, kind: str, extension: str, cancel_check: Callable[[], bool] | None = None, require_audio: bool = False
) -> dict[str, Any]:
  """Require a nonempty requested container and its expected media streams."""
  if not path.is_file() or path.stat().st_size == 0:
    raise DownloadError('error.download.media_validation_failed')
  details = read_media_streams(path, cancel_check=cancel_check)
  formats = set(str(details.get('format', {}).get('format_name') or '').split(','))
  containers = {
    'm4a': {'mov', 'mp4'},
    'mp4': {'mov', 'mp4'},
    'mov': {'mov', 'mp4'},
    'mkv': {'matroska'},
    'webm': {'webm'},
    'aac': {'aac'},
  }
  if extension and not formats.intersection(containers.get(extension, {extension})):
    raise DownloadError('error.download.media_validation_failed')
  streams = details['streams']
  audio = [stream for stream in streams if stream.get('codec_type') == 'audio']
  video = [
    stream
    for stream in streams
    if stream.get('codec_type') == 'video' and not stream.get('disposition', {}).get('attached_pic')
  ]
  if (
    ((kind == 'audio' or require_audio) and not audio) or (kind == 'video' and not video) or (kind == 'audio' and video)
  ):
    raise DownloadError('error.download.media_validation_failed')
  return details


def canonical_audio_language(language: str) -> str:
  """Map ISO language aliases while retaining a source's regional distinction."""
  language = str(language or '').strip().lower().replace('_', '-')
  base, separator, region = language.partition('-')
  base = get_global('language_aliases').get(base, base)
  return base + (separator + region if separator else '')


def tag_audio_language(
  path: Path, *, language: str, role: str = '', cancel_check: Callable[[], bool] | None = None
) -> None:
  """Preserve a known selected language in containers that support stream tags."""
  if not language or path.suffix.lower() not in {'.m4a', '.mp4', '.mkv', '.webm', '.mov', '.ogg', '.flac', '.mp3'}:
    return
  tagged = path.with_name(path.stem + '.tagged' + path.suffix)
  base = canonical_audio_language(language).split('-')[0]
  iso_language = next(
    (key for key, value in get_global('language_aliases').items() if value == base and len(key) == 3), base
  )
  metadata = ['-metadata:s:a:0', 'language=' + iso_language]
  metadata += ['-metadata', 'language=' + iso_language, '-metadata', 'source_language=' + language]
  if role:
    metadata += ['-metadata:s:a:0', 'title=' + role]
  if role == 'descriptive':
    metadata += ['-disposition:a:0', '+visual_impaired']
  try:
    run_media_command(
      [
        resolve_ffmpeg_tool(AppConfig, 'ffmpeg'),
        '-v',
        'error',
        '-nostdin',
        '-y',
        '-i',
        str(path),
        '-map',
        '0',
        '-c',
        'copy',
        *metadata,
        str(tagged),
      ],
      cancel_check=cancel_check,
    )
    details = read_media_streams(tagged, cancel_check=cancel_check)
    audio = next((stream for stream in details['streams'] if stream.get('codec_type') == 'audio'), {})
    language_tag = (audio.get('tags') or {}).get('language') or details.get('format', {}).get('tags', {}).get(
      'language'
    )
    if canonical_audio_language(str(language_tag or '')).split('-')[0] != base:
      raise DownloadError('error.download.media_validation_failed')
    tagged.replace(path)
  finally:
    tagged.unlink(missing_ok=True)


def limit_audio_bitrate(path: Path, *, quality: str, cancel_check: Callable[[], bool] | None = None) -> None:
  """Transcode only when a requested lossy bitrate cannot be copied from the source."""
  match = re.fullmatch(r'(\d+)(?:k|kbps)?', str(quality or '').strip().lower())
  codecs = {'.m4a': 'aac', '.aac': 'aac', '.mp3': 'libmp3lame', '.ogg': 'libvorbis'}
  if not match or path.suffix.lower() not in codecs:
    return
  bitrate = int(match.group(1)) * 1000
  streams = read_media_streams(path, cancel_check=cancel_check)['streams']
  audio = next((stream for stream in streams if stream.get('codec_type') == 'audio'), {})
  if 0 < int(audio.get('bit_rate') or 0) <= bitrate * 1.1:
    return
  converted = path.with_name(path.stem + '.bitrate' + path.suffix)
  try:
    run_media_command(
      [
        resolve_ffmpeg_tool(AppConfig, 'ffmpeg'),
        '-v',
        'error',
        '-nostdin',
        '-y',
        '-i',
        str(path),
        '-map',
        '0:a:0',
        '-c:a',
        codecs[path.suffix.lower()],
        '-b:a',
        str(bitrate),
        str(converted),
      ],
      cancel_check=cancel_check,
    )
    converted.replace(path)
  finally:
    converted.unlink(missing_ok=True)
