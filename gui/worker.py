# gui/worker.py
# Worker transkrypcji obsługujący w JEDNEJ liście zarówno pliki lokalne, jak i URL-e.
# Obsługa kolizji wyników: Skip / Nowa wersja / Nadpisz (synchronizacja z GUI).
# Pre-check dla URL-i (bez pobierania) – oszczędza łącze.
# Natychmiastowe przerwanie: cancel() + możliwość twardego zakończenia przez terminate() po stronie MainWindow.

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional, Iterable, List, Tuple

from PyQt5 import QtCore

from core.config import Config
from core.downloader import Downloader


class Worker(QtCore.QObject):
    # Logi/progres
    log = QtCore.pyqtSignal(str)
    progress = QtCore.pyqtSignal(int)
    finished = QtCore.pyqtSignal()

    # Prośba o decyzję przy kolizji: stem (nazwa bazowa) i istniejący katalog (str)
    conflict_check = QtCore.pyqtSignal(str, str)

    def __init__(
        self,
        model_manager=None,
        files: Optional[Iterable[Path]] = None,  # zachowane dla kompatybilności (nieużywane w nowym przepływie)
        pipe=None,
        entries: Optional[List[dict]] = None,    # [{'type':'file'|'url', 'value': str}]
        parent: Optional[QtCore.QObject] = None,
    ):
        super().__init__(parent)
        self.model_manager = model_manager
        self.pipe = pipe
        self._cancelled = False

        # Nowe: wpisy mieszane (plik/URL). Jeśli nie podano, budujemy z 'files'.
        if entries is not None:
            self.entries = list(entries)
        else:
            # fallback: z listy 'files'
            self.entries = [{"type": "file", "value": str(Path(p))} for p in (files or [])]

        # Pola do synchronizacji decyzji GUI
        self._decision_loop: Optional[QtCore.QEventLoop] = None
        self._decision_result: Optional[Tuple[str, str]] = None  # (action: skip/new/overwrite, new_stem)

    # ------------- API sterujące -------------

    def cancel(self) -> None:
        """
        Łagodne anulowanie — pętla główna sprawdza ten znacznik.
        MainWindow w razie potrzeby wykona terminate() na wątku dla natychmiastowego zabicia.
        """
        self._cancelled = True
        self.log.emit("⏹️ Żądanie anulowania – zatrzymywanie…")

    # Slot wywoływany z GUI po pokazaniu dialogu
    @QtCore.pyqtSlot(str, str)
    def on_conflict_decided(self, action: str, new_stem: str) -> None:
        # action in {"skip","new","overwrite"}
        self._decision_result = (action, new_stem)
        if self._decision_loop is not None:
            self._decision_loop.quit()

    # ------------- Pomocnicze -------------

    def _log_runtime_mode(self) -> None:
        mode = "GPU" if Config.DEVICE.type == "cuda" else "CPU"
        dtype_name = str(Config.DTYPE).split(".")[-1]
        tf32 = "ON" if Config.TF32_ENABLED else "OFF"
        dev_name = Config.DEVICE_FRIENDLY_NAME
        msg = (
            f"🧠 Tryb: {mode}{f' ({dev_name})' if mode == 'GPU' else ''}, "
            f"dtype={dtype_name}, TF32={tf32}, źródła={len(self.entries)}"
        )
        self.log.emit(msg)

    def _should_abort(self) -> bool:
        return self._cancelled or (QtCore.QThread.currentThread().isInterruptionRequested())

    def _ask_conflict_resolution(self, stem: str) -> Tuple[str, str]:
        """
        Prosi GUI o decyzję. Zwraca (action, new_stem).
        action ∈ {'skip','new','overwrite'}
        new_stem używane tylko dla 'new' (może być pusty dla pozostałych akcji).
        """
        existing_dir = str((Config.OUTPUT_DIR / stem))
        self._decision_result = None
        self._decision_loop = QtCore.QEventLoop()
        # emit -> MainWindow pokaże dialog i oddzwoni on_conflict_decided(...)
        self.conflict_check.emit(stem, existing_dir)
        self._decision_loop.exec_()
        self._decision_loop = None
        if self._decision_result is None:
            # awaryjnie pomiń
            return "skip", ""
        return self._decision_result

    @staticmethod
    def _next_free_stem(base_stem: str) -> str:
        """
        Znajduje pierwszy wolny stem w postaci 'name (n)'.
        """
        candidate = base_stem
        n = 1
        while (Config.OUTPUT_DIR / candidate).exists():
            candidate = f"{base_stem} ({n})"
            n += 1
        return candidate

    # ------------- Główna praca -------------

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            self._log_runtime_mode()

            asr_pipe = self.pipe or (getattr(self.model_manager, "pipe", None) if self.model_manager else None)
            if asr_pipe is None:
                self.log.emit("⚠️ Brak gotowego pipeline — przerwanie zadania.")
                self.finished.emit()
                return

            total = len(self.entries)
            for idx, entry in enumerate(self.entries, start=1):
                if self._should_abort():
                    break

                etype = (entry.get("type") or "").lower()
                value = entry.get("value") or ""

                # 1) Ustal wstępny 'stem' (dla porównania w OUTPUT_DIR)
                if etype == "file":
                    p = Path(value)
                    if not (p.exists() and p.is_file()):
                        self.log.emit(f"⚠️ Pomijam: nie znaleziono pliku: {p}")
                        continue
                    base_stem = p.stem
                elif etype == "url":
                    self.log.emit(f"🌐 Analiza URL (bez pobierania): {value}")
                    try:
                        base_stem = Downloader.peek_output_stem(value, log=self.log.emit) or "plik"
                    except Exception:
                        base_stem = "plik"
                else:
                    self.log.emit(f"⚠️ Nieznany typ wpisu: {etype}")
                    continue

                if self._should_abort():
                    break

                # 2) Sprawdź kolizję w OUTPUT_DIR
                chosen_stem = base_stem
                out_dir = Config.OUTPUT_DIR / chosen_stem
                if out_dir.exists():
                    self.log.emit(f"⚠️ Wykryto istniejący folder wyjściowy: {out_dir}")
                    action, new_stem = self._ask_conflict_resolution(base_stem)
                    if action == "skip":
                        self.log.emit("⏭️ Pominieto na żądanie użytkownika.")
                        self.progress.emit(int(idx * 100 / max(1, total)))
                        continue
                    elif action == "overwrite":
                        try:
                            shutil.rmtree(str(out_dir), ignore_errors=True)
                            self.log.emit("♻️ Nadpisywanie — usunięto poprzednią wersję.")
                        except Exception as e:
                            self.log.emit(f"❗ Nie udało się usunąć poprzedniej wersji: {e}")
                        chosen_stem = base_stem
                    elif action == "new":
                        chosen_stem = new_stem or self._next_free_stem(base_stem)
                        self.log.emit(f"🆕 Tworzenie nowej wersji: {chosen_stem}")

                if self._should_abort():
                    break

                # 3) Pozyskaj lokalne ścieżki do transkrypcji
                local_paths: List[Path] = []
                if etype == "file":
                    local_paths = [Path(value)]
                elif etype == "url":
                    self.log.emit(f"🌐 Pobieranie: {value}")
                    try:
                        def _dlog(m: str) -> None:
                            self.log.emit(m)
                        dl_paths = Downloader.download(urls=[value], on_file_ready=None, log=_dlog)
                        local_paths = [Path(p) for p in dl_paths]
                        if not local_paths:
                            self.log.emit(f"❌ Błąd pobierania: Brak plików po pobraniu.")
                            self.progress.emit(int(idx * 100 / max(1, total)))
                            continue
                    except Exception as e:
                        self.log.emit(f"❌ Błąd pobierania: {e}")
                        self.progress.emit(int(idx * 100 / max(1, total)))
                        continue

                # 4) Transkrypcja i zapis
                for p in local_paths:
                    if self._should_abort():
                        break
                    self.log.emit(f"🎧 Transkrypcja: {p.name}")
                    try:
                        # Długie pliki: long-form wymaga return_timestamps=True
                        result = asr_pipe(str(p), return_timestamps=True)
                    except Exception as e:
                        self.log.emit(f"❗ Błąd transkrypcji {p.name}: {e}")
                        continue

                    if self._should_abort():
                        break

                    text = ""
                    if isinstance(result, dict) and "text" in result:
                        text = result["text"]
                    else:
                        text = str(result)

                    try:
                        out_dir = (Config.OUTPUT_DIR / chosen_stem)
                        out_dir.mkdir(parents=True, exist_ok=True)
                        out_path = out_dir / "transcript.txt"
                        with out_path.open("w", encoding="utf-8") as f:
                            f.write(text)
                        self.log.emit(f"✅ Zapisano: {out_path}")
                    except Exception as e:
                        self.log.emit(f"❗ Błąd zapisu transkryptu dla {p.name}: {e}")

                self.progress.emit(int(idx * 100 / max(1, total)))

        finally:
            self.finished.emit()
