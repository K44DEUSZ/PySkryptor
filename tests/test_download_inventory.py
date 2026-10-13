from app.features.download.inventory import TrackInventory


def test_direct_media_with_unknown_codecs_remains_downloadable():
  info = {'formats': [{'url': 'https://example.test/owned.mp4', 'ext': 'mp4', 'format_id': 'mp4'}]}
  counts = TrackInventory.downloadable_media_counts(info)
  assert counts['media_format_count'] == 1 and counts['unknown_media_format_count'] == 1
  assert counts['combined_format_count'] == 0


def test_storyboards_and_missing_urls_are_not_downloadable_media():
  info = {
    'formats': [
      {'url': 'https://example.test/storyboard.jpg', 'ext': 'jpg', 'resolution': 'storyboard'},
      {'ext': 'mp4'},
      {'url': 'https://example.test/empty.mp4', 'ext': 'mp4', 'acodec': 'none', 'vcodec': 'none'},
    ]
  }
  counts = TrackInventory.downloadable_media_counts(info)
  assert counts['media_format_count'] == 0 and counts['image_only_format_count'] == 1


def test_hls_audio_declaration_does_not_require_a_named_codec():
  fmt = {'format_id': 'audio-Polski', 'vcodec': 'none', 'audio_ext': 'mp4', 'language': 'pl'}
  assert TrackInventory.has_audio(fmt) and not TrackInventory.has_video(fmt)
