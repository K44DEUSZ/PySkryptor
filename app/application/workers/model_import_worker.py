# app/application/workers/model_import_worker.py
from __future__ import annotations

from pathlib import Path

from PyQt6 import QtCore

from app.application.workers.task_worker import TaskWorker
from app.runtime.model_catalog import ModelCatalogService


class ModelImportWorker(TaskWorker):
  """Validate a local model folder outside the GUI thread and register its reference."""

  model_added = QtCore.pyqtSignal(object)

  def __init__(self, folder: str, role: str) -> None:
    super().__init__()
    self._folder = Path(folder)
    self._role = role

  def _execute(self) -> None:
    if self.cancel_check():
      return
    model = ModelCatalogService().register(self._folder, expected_role=self._role)
    if not self.cancel_check():
      self.model_added.emit(model)
