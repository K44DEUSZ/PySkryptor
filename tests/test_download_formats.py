import pytest

from app.features.download.plan import DownloadPlanBuilder


@pytest.mark.parametrize('extension, source_extension, codec', [('ogg', 'opus', 'vorbis'), ('aac', 'm4a', 'aac')])
def test_compatible_input_does_not_skip_requested_audio_conversion(extension, source_extension, codec):
  info = {'formats': [{'format_id': 'a', 'ext': source_extension, 'acodec': 'aac', 'vcodec': 'none'}]}
  plan = DownloadPlanBuilder.build_audio_plan(
    info=info,
    quality='auto',
    ext_l=extension,
    lang_base='',
    purpose='download',
    keep_output=True,
  )
  assert plan['postprocessors'][0]['preferredcodec'] == codec
  if extension == 'aac':
    assert plan['postprocessors'][-1] == {'key': 'FFmpegVideoRemuxer', 'preferedformat': 'aac'}
