import csv
import io
import json

import pytest

from app.features.transcripts.policy import TranscriptionOutputPolicy as Policy
from app.features.transcripts.writer import TranscriptWriter as Writer

SEGMENTS = [{'start': 3661.125, 'end': 3663.25, 'text': 'Zażółć, "gęślą" jaźń.'}]


def render(mode, **kwargs):
  return Writer.render_output(
    merged_text='Zażółć, "gęślą" jaźń.',
    translated_text='',
    translated_segments=None,
    segments=SEGMENTS,
    mode=Policy.get_transcription_output_mode(mode),
    **kwargs,
  )


def test_vtt_preserves_text_and_uses_subtitle_timing():
  assert render('vtt') == 'WEBVTT\n\n1\n01:01:01.125 --> 01:01:03.250\nZażółć, "gęślą" jaźń.\n'


def test_json_retains_unicode_and_numeric_times():
  parsed = json.loads(render('json'))
  assert parsed['text'] == SEGMENTS[0]['text']
  assert parsed['segments'] == SEGMENTS
  assert parsed['schema_version'] == 1


def test_csv_roundtrips_quotes_commas_and_unicode():
  rows = list(csv.DictReader(io.StringIO(render('csv'))))
  assert rows == [{'start_seconds': '3661.125', 'end_seconds': '3663.25', 'text': SEGMENTS[0]['text']}]


def test_translation_exports_use_translated_segments():
  output = Writer.render_output(
    merged_text='Oryginał',
    translated_text='Translation',
    translated_segments=[{'start': 1, 'end': 2, 'text': 'Translation'}],
    segments=SEGMENTS,
    mode=Policy.get_transcription_output_mode('vtt'),
  )
  assert 'Translation' in output and 'Zażółć' not in output
  assert '00:00:01.000 --> 00:00:02.000' in output


def test_all_formats_are_written_to_distinct_files(tmp_path):
  outputs = Writer.write_mode_outputs(
    out_dir=tmp_path,
    output_mode_ids=Policy.valid_mode_ids(),
    mode_resolver=Policy.get_transcription_output_mode,
    filename_resolver=Policy.transcript_filename,
    unique_path_resolver=lambda path: path,
    merged_text=SEGMENTS[0]['text'],
    translated_text='',
    translated_segments=None,
    segments=SEGMENTS,
  )
  assert len(outputs) == len(set(outputs)) == 7
  assert all(path.exists() and path.read_text(encoding='utf-8') for path in outputs)
  assert {path.suffix for path in outputs} == {'.txt', '.srt', '.vtt', '.json', '.csv', '.md'}


@pytest.mark.parametrize('extension', ['txt', 'md', 'json', 'csv'])
def test_live_translation_and_source_companion_use_selected_format(tmp_path, extension):
  outputs = Writer.save_live_transcript(
    target_path=str(tmp_path / f'wynik.{extension}'),
    source_text='Cześć, świecie!',
    target_text='Hello, world!',
    write_source_companion=True,
  )
  assert len(outputs) == 2
  assert outputs[1].name == f'wynik_og.{extension}'
  translated = outputs[0].read_text(encoding='utf-8')
  original = outputs[1].read_text(encoding='utf-8')
  if extension == 'json':
    assert json.loads(translated)['text'] == 'Hello, world!'
    assert json.loads(original)['segments'] == []
  elif extension == 'csv':
    row = next(csv.DictReader(io.StringIO(original)))
    assert row == {'start_seconds': '', 'end_seconds': '', 'text': 'Cześć, świecie!'}
  else:
    assert 'Hello, world!' in translated and 'Cześć, świecie!' in original


def test_markdown_preserves_literal_markup_as_text():
  output = Writer.render_output(
    merged_text='[link](adres)\n# tytuł',
    translated_text='',
    translated_segments=None,
    segments=[],
    mode=Policy.get_transcription_output_mode('md'),
  )
  assert output == '\\[link\\](adres)\n\n\\# tytuł\n'


def test_empty_live_transcript_creates_no_file(tmp_path):
  path = tmp_path / 'empty.json'
  assert (
    Writer.save_live_transcript(target_path=str(path), source_text='', target_text='', write_source_companion=True)
    == []
  )
  assert not path.exists()
