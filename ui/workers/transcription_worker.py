# ui/workers/transcription_worker.py
from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import List, Optional, Tuple, Union, Dict, Any

from PyQt5 import QtCore

from core.config.app_config import AppConfig as Config
from core.files.file_manager import FileManager
from core.services.download_service import DownloadService
from core.transcription.text_postprocessor import TextPostprocessor
from core.utils.text import is_url, sanitize_filename


GUIEntry = Union[str, Dict[str, Any]]
WorkItem = Tuple[str, Path, Optional[str]]  # (key, local_path, forced_stem)


class TranscriptionWorker(QtCore.QObject):
    """
    Transcribes a queue of entries. Emits fine-grained status updates so the
    'Details table' in the UI can show per-item states and a transcript opener.
    """
    log = QtCore.pyqtSignal(str)
    progress = QtCore.pyqtSignal(int)
    finished = QtCore.pyqtSignal()

    conflict_check = QtCore.pyqtSignal(str, str)  # stem, existing_dir

    item_status = QtCore.pyqtSignal(str, str)  # key, status label to show in table
    item_path_update = QtCore.pyqtSignal(str, str)  # old_key, new_local_path (when URL becomes a file)
    transcript_ready = QtCore.pyqtSignal(str, str)  # key, transcript_path

    def __init__(self, files: Optional[List[Path]] = None, pipe=None, entries: Optional[List[GUIEntry]] = None) -> None:
        super().__init__()
        self._cancel = threading.Event()
        self._pipe = pipe
        self._raw_entries: List[GUIEntry] = list(entries or [])
        self._download = DownloadService()

        # conflict dialog state (synchronous rendezvous)
        self._conflict_event = threading.Event()
        self._conflict_action: Optional[str] = None  # "skip" | "overwrite" | "new"
        self._conflict_new_stem: str = ""

    def cancel(self) -> None:
        self._cancel.set()

    @QtCore.pyqtSlot()
    def run(self) -> None:
        processed_any = False
        try:
            # Fresh temp each run
            try:
                if Config.INPUT_TMP_DIR.exists():
                    shutil.rmtree(Config.INPUT_TMP_DIR, ignore_errors=True)
                Config.INPUT_TMP_DIR.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self.log.emit(f"❗ Nie udało się przygotować katalogu tymczasowego: {e}")

            # Plan (do not create yet) timestamped output session
            try:
                planned = FileManager.plan_session()
                self.log.emit(f"🗂️ Sesja wynikowa (plan): {planned}")
            except Exception as e:
                self.log.emit(f"❗ Nie udało się zaplanować katalogu sesji: {e}")

            # 1) Build work list
            work_items: List[WorkItem] = []
            for entry in self._raw_entries:
                if self._cancel.is_set():
                    break
                try:
                    items = self._materialize_entry(entry)
                    work_items.extend(items)
                except Exception as e:
                    self.log.emit(f"❗ Błąd przygotowania pozycji „{entry}”: {e}")

            total = len(work_items)
            if total == 0:
                self.log.emit("ℹ️ Brak pozycji do przetworzenia.")
                return

            # 2) Process items
            for idx, (key, path, forced_stem) in enumerate(work_items, start=1):
                if self._cancel.is_set():
                    break

                stem = sanitize_filename(forced_stem) if forced_stem else sanitize_filename(path.stem)

                # Cross-session conflict check
                existing = FileManager.find_existing_output(stem)

                # Decide target dir without creating yet
                out_dir = FileManager.output_dir_for(stem)
                write_into_existing = False
                if existing is not None:
                    if not out_dir.exists() or existing.resolve() != out_dir.resolve():
                        try:
                            self._conflict_event.clear()
                            self.conflict_check.emit(stem, str(existing))
                            self._conflict_event.wait()
                        except Exception as e:
                            self.log.emit(f"❗ Błąd okna konfliktu: {e} — pomijam.")
                            self._set_conflict_decision("skip", "")

                        if self._conflict_action == "skip":
                            self.item_status.emit(key, "Pominięto")
                            self.log.emit(f"⏭️ Pomijam „{path.name}” (konflikt).")
                            self.progress.emit(int(idx * 100 / total))
                            continue
                        elif self._conflict_action == "new":
                            if self._conflict_new_stem:
                                stem = sanitize_filename(self._conflict_new_stem)
                                out_dir = FileManager.output_dir_for(stem)
                        elif self._conflict_action == "overwrite":
                            out_dir = Path(existing)
                            write_into_existing = True

                # Prepare WAV 16k mono
                try:
                    self.item_status.emit(key, "Przygotowanie audio…")
                    wav = FileManager.ensure_tmp_wav(path, log=lambda m: None)
                    self.log.emit(f"🎛️ Przygotowano audio: {Path(wav).name}")
                except Exception as e:
                    self.item_status.emit(key, "Błąd przygotowania")
                    self.log.emit(f"❗ Błąd przygotowania audio dla {path.name}: {e}")
                    self.progress.emit(int(idx * 100 / total))
                    continue

                if self._cancel.is_set():
                    break

                # Run ASR
                self.item_status.emit(key, "Przetwarzanie…")
                self.log.emit(f"🎧 Transkrypcja: {Path(wav).name}")
                try:
                    result = self._pipe(
                        str(wav),
                        chunk_length_s=60,
                        stride_length_s=5,
                        return_timestamps=True,
                        generate_kwargs={"task": "transcribe"},
                        ignore_warning=True,
                    )
                    text = result["text"] if isinstance(result, dict) and "text" in result else str(result)
                    text = TextPostprocessor.clean(text)
                except Exception as e:
                    self.item_status.emit(key, "Błąd transkrypcji")
                    self.log.emit(f"❗ Błąd transkrypcji {path.name}: {e}")
                    self.progress.emit(int(idx * 100 / total))
                    continue

                # Create session dir lazily only if we are going to write into a new session
                try:
                    if not write_into_existing:
                        FileManager.ensure_session()
                except Exception as e:
                    self.item_status.emit(key, "Błąd zapisu")
                    self.log.emit(f"❗ Nie udało się utworzyć katalogu sesji: {e}")
                    self.progress.emit(int(idx * 100 / total))
                    continue

                # Save transcript
                created_dir = False
                try:
                    if not out_dir.exists():
                        out_dir.mkdir(parents=True, exist_ok=True)
                        created_dir = True
                    out_txt = out_dir / "transcript.txt"
                    out_txt.write_text(text, encoding="utf-8")
                    self.log.emit(f"💾 Zapisano transkrypt: {out_txt}")
                    self.item_status.emit(key, "Gotowe")
                    self.transcript_ready.emit(key, str(out_txt))
                    processed_any = True
                except Exception as e:
                    self.item_status.emit(key, "Błąd zapisu")
                    self.log.emit(f"❗ Błąd zapisu transkryptu dla {path.name}: {e}")
                    if created_dir:
                        FileManager.remove_dir_if_empty(out_dir)

                self.progress.emit(int(idx * 100 / total))

        except Exception as e:
            self.log.emit(f"❗ Nieoczekiwany błąd w workerze transkrypcji: {e}")
        finally:
            try:
                if Config.INPUT_TMP_DIR.exists():
                    shutil.rmtree(Config.INPUT_TMP_DIR, ignore_errors=True)
                    self.log.emit("🧹 Wyczyszczono katalog tymczasowy.")
            except Exception as e:
                self.log.emit(f"⚠️ Problem z czyszczeniem katalogu tymczasowego: {e}")

            if not processed_any:
                FileManager.rollback_session_if_empty()

            FileManager.end_session()
            self.finished.emit()

    # ----- Conflict decision rendezvous -----

    @QtCore.pyqtSlot(str, str)
    def on_conflict_decided(self, action: str, new_stem: str = "") -> None:
        self._set_conflict_decision(action, new_stem)

    def _set_conflict_decision(self, action: str, new_stem: str) -> None:
        self._conflict_action = action
        self._conflict_new_stem = new_stem
        self._conflict_event.set()

    # ----- Helpers -----

    def _normalize_entry(self, raw: GUIEntry) -> Tuple[str, str]:
        if isinstance(raw, dict):
            t = str(raw.get("type", "") or "").strip().lower()
            v = raw.get("value", "")
            v = str(v) if not isinstance(v, str) else v
            return v.strip(), t
        s = str(raw).strip()
        if s.startswith("[URL]"):
            return s[5:].strip(), "url"
        return s, ""

    def _materialize_entry(self, raw: GUIEntry) -> List[WorkItem]:
        """
        Returns list of (key, local_path, forced_stem).
        For URLs: performs pre-download conflict check using title, then downloads.
        """
        s, t = self._normalize_entry(raw)

        # URL path
        if t == "url" or is_url(s):
            key = s  # for status mapping in UI
            self.log.emit(f"🌐 Analiza URL (bez pobierania): {s}")
            self.item_status.emit(key, "Analiza…")
            try:
                meta = self._download.probe(s, log=lambda m: None)
            except Exception as e:
                self.item_status.emit(key, "Błąd analizy")
                self.log.emit(f"❗ Błąd analizy URL: {e}")
                return []
            if self._cancel.is_set():
                return []

            # Predict stem and check conflicts BEFORE downloading
            title = meta.get("title") or "plik"
            predicted_stem = sanitize_filename(Path(title).stem)
            existing = FileManager.find_existing_output(predicted_stem)
            forced_stem: Optional[str] = None

            if existing is not None:
                try:
                    self._conflict_event.clear()
                    self.conflict_check.emit(predicted_stem, str(existing))
                    self._conflict_event.wait()
                except Exception as e:
                    self.log.emit(f"❗ Błąd okna konfliktu: {e} — pomijam.")
                    self._set_conflict_decision("skip", "")

                if self._conflict_action == "skip":
                    self.item_status.emit(key, "Pominięto")
                    self.log.emit("⏭️ Pomijam (konflikt wykryty przed pobraniem).")
                    return []
                elif self._conflict_action == "new":
                    if self._conflict_new_stem:
                        forced_stem = sanitize_filename(self._conflict_new_stem)
                elif self._conflict_action == "overwrite":
                    forced_stem = predicted_stem

            if self._cancel.is_set():
                return []

            self.log.emit(f"🌐 Pobieranie: {s}")
            self.item_status.emit(key, "Pobieranie…")
            try:
                # POPRAWKA: przekazujemy pojedynczy 'url', bez parametru 'urls'
                result_path = self._download.download(
                    url=s,
                    on_file_ready=None,
                )
                # obsługa przypadku gdy download zwraca listę ścieżek lub jedną ścieżkę
                local = result_path[-1] if isinstance(result_path, list) else result_path
                if local:
                    self.item_path_update.emit(key, str(local))
                    new_key = str(local)
                    return [(new_key, local, forced_stem)]
                self.item_status.emit(key, "Błąd pobierania")
                self.log.emit("❗ Błąd pobierania: brak pliku.")
                return []
            except Exception as e:
                self.item_status.emit(key, "Błąd pobierania")
                self.log.emit(f"❌ Błąd pobierania: {e}")
                return []

        # Local directory
        p = Path(s)
        if p.is_dir():
            files = [x for x in p.rglob("*") if
                     x.is_file() and x.suffix.lower() in (Config.AUDIO_EXT | Config.VIDEO_EXT)]
            if not files:
                self.log.emit(f"⚠️ Brak obsługiwanych plików w folderze: {p}")
            return [(str(f), f, None) for f in sorted(files)]

        # Local file
        if p.is_file():
            return [(str(p), p, None)]

        self.log.emit(f"⚠️ Nie znaleziono ścieżki: {p}")
        return []
