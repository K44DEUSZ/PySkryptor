# app/features/download/direct_media.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.core.config.config import AppConfig
from app.core.runtime.ffmpeg import resolve_ffmpeg_tool
from app.features.download.domain import DownloadError
from app.features.download.media_validation import canonical_audio_language, read_media_streams, run_media_command


def inspect_direct_tracks(
  info: dict[str, Any],
  *,
  cancel_check: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
  """Expose internal audio streams of direct media served as one format."""
  formats = info.get('formats') or []
  if str(info.get('extractor_key') or info.get('extractor') or '').lower() != 'generic' or len(formats) != 1:
    return []
  source = formats[0]
  if str(source.get('ext') or '').lower() not in {'mp4', 'mkv', 'mov', 'webm', 'avi'}:
    return []
  try:
    details = read_media_streams(str(source.get('url') or info.get('url') or ''), cancel_check=cancel_check)
  except DownloadError:
    return []
  tracks = []
  for stream in details['streams']:
    if stream.get('codec_type') != 'audio':
      continue
    tags = stream.get('tags') or {}
    language = canonical_audio_language(str(tags.get('language') or ''))
    if language == 'und':
      language = ''
    index = int(stream['index'])
    tracks.append(
      {
        'track_id': f'stream-{index}',
        'track_key': f'stream-{index}',
        'stream_index': index,
        'lang_code': language,
        'label_source': str(tags.get('title') or ''),
        'role': 'descriptive' if stream.get('disposition', {}).get('visual_impaired') else '',
        'is_default': bool(stream.get('disposition', {}).get('default')),
        'candidates': [
          {
            'format_id': source['format_id'],
            'ext': source.get('ext'),
            'has_audio': True,
            'has_video': any(item.get('codec_type') == 'video' for item in details['streams']),
            'abr': float(stream.get('bit_rate') or 0) / 1000,
            'probe_client': 'default',
          }
        ],
      }
    )
  return tracks


def extract_direct_track(
  path: Path,
  *,
  track: dict[str, Any],
  kind: str,
  extension: str,
  quality: str,
  cancel_check: Callable[[], bool] | None = None,
) -> Path:
  """Map an explicitly selected internal stream before any format conversion."""
  details = read_media_streams(path, cancel_check=cancel_check)
  index = int(track['stream_index'])
  stream = next((item for item in details['streams'] if item.get('index') == index), {})
  expected_language = canonical_audio_language(str(track.get('lang_code') or '')).split('-')[0]
  actual_language = canonical_audio_language(str(stream.get('tags', {}).get('language') or '')).split('-')[0]
  if stream.get('codec_type') != 'audio' or (expected_language and actual_language != expected_language):
    raise DownloadError('error.download.audio_track_changed')
  extension = extension or ('m4a' if kind == 'audio' else path.suffix.lstrip('.'))
  output = path.with_name(path.stem + '.selected.' + extension)
  arguments = [resolve_ffmpeg_tool(AppConfig, 'ffmpeg'), '-v', 'error', '-nostdin', '-y', '-i', str(path)]
  audio_codecs = {
    'mp3': 'libmp3lame',
    'm4a': 'aac',
    'aac': 'aac',
    'ogg': 'libvorbis',
    'flac': 'flac',
    'wav': 'pcm_s16le',
  }
  if kind == 'audio':
    arguments += ['-map', f'0:{index}', '-vn']
    copy_codecs = {'mp3': 'mp3', 'm4a': 'aac', 'aac': 'aac', 'ogg': 'vorbis', 'flac': 'flac'}
    codec = 'copy' if stream.get('codec_name') == copy_codecs.get(extension) else audio_codecs[extension]
    arguments += ['-c:a', codec]
  else:
    arguments += ['-map', '0:v:0', '-map', f'0:{index}']
    height = str(quality or '').lower().removesuffix('p')
    target_height = int(height) if height.isdigit() and int(height) > 0 else AppConfig.downloader_max_video_height()
    target_height = max(
      AppConfig.downloader_min_video_height(), min(target_height, AppConfig.downloader_max_video_height())
    )
    video_stream = next((item for item in details['streams'] if item.get('codec_type') == 'video'), {})
    resize = int(video_stream.get('height') or 0) > target_height
    if not resize and (extension == path.suffix.lstrip('.') or extension == 'mkv'):
      arguments += ['-c', 'copy']
    else:
      video, audio = {'webm': ('libvpx-vp9', 'libopus'), 'avi': ('mpeg4', 'libmp3lame')}.get(
        extension,
        ('libx264', 'aac'),
      )
      arguments += ['-c:v', video, '-c:a', audio]
      if resize:
        arguments += ['-vf', f'scale=-2:min(ih\\,{target_height})']
  run_media_command([*arguments, str(output)], cancel_check=cancel_check)
  path.unlink()
  return output
