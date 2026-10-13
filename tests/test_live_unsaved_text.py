from types import SimpleNamespace

from PyQt6 import QtWidgets

from app.ui.panels.live_panel import LivePanel


def test_saving_live_text_clears_warning_and_later_changes_restore_it(monkeypatch, qt_app, tmp_path):
  panel = LivePanel()
  texts = {'source': 'Tekst z nagrania.', 'target': ''}
  monkeypatch.setattr(panel, '_current_session_texts', lambda: (texts['source'], texts['target']))
  monkeypatch.setattr(panel, '_coordinator_is_running', lambda: False)
  monkeypatch.setattr(panel, '_is_translate_mode_effective', lambda: False)
  monkeypatch.setattr(panel, 'coordinator', lambda: SimpleNamespace(save_transcript=lambda **_kwargs: []))
  monkeypatch.setattr(
    QtWidgets.QFileDialog,
    'getSaveFileName',
    lambda *_args: (str(tmp_path / 'tekst.txt'), 'TXT (*.txt)'),
  )
  assert panel.has_unsaved_output()
  panel._save_transcript()
  assert not panel.has_unsaved_output()
  texts['source'] += ' Kolejne zdanie.'
  assert panel.has_unsaved_output()
  panel.deleteLater()
