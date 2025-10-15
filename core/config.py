# core/config.py
# Konfiguracja środowiska: wybór urządzenia (CPU/GPU), dtype, TF32, ścieżki, integracje FFmpeg.
# Zostawia kompatybilność CPU-only (FORCE_CPU=1) i dodaje jawne logowanie trybu pracy.

import os
import platform
import shutil
from pathlib import Path

import torch


class Config:
    """
    Centralna konfiguracja aplikacji.
    Zostawiamy obsługę FORCE_CPU, doprecyzowujemy wybór DEVICE/DTYPE i TF32.
    """

    # --- ŚCIEŻKI PROJEKTU I MODELI (dostosuj, jeśli masz inne w projekcie) ---
    ROOT_DIR = Path(__file__).resolve().parents[1]
    FILES_DIR = ROOT_DIR / "files"
    INPUT_DIR = FILES_DIR / "input"
    OUTPUT_DIR = FILES_DIR / "output"

    MODELS_DIR = ROOT_DIR / ".models"
    WHISPER_TURBO_DIR = MODELS_DIR / "whisper-turbo"

    # FFmpeg
    FFMPEG_DIR = ROOT_DIR / ".ffmpeg" / "bin"

    # --- FLAGI ŚRODOWISKOWE ---
    # FORCE_CPU=1 w zmiennych środowiskowych wymusi CPU
    FORCE_CPU = int(os.getenv("FORCE_CPU", "0")) == 1

    # --- WYBRANE PARAMETRY OBLICZENIOWE (USTALANE W initialize()) ---
    DEVICE = torch.device("cpu")
    DTYPE = torch.float32
    DEVICE_FRIENDLY_NAME = "CPU"
    TF32_ENABLED = False

    @staticmethod
    def initialize() -> None:
        """
        Przygotowanie środowiska:
        - PATH dla FFmpeg (Windows i ogólny PATH),
        - wybór urządzenia (FORCE_CPU -> CPU, w przeciwnym razie CUDA jeśli dostępne),
        - ustawienie dtype (CPU=float32, GPU=float16/bfloat16),
        - włączenie TF32 na GPU (jeśli możliwe),
        - globalna precyzja dla float32 matmul (medium).
        """
        # --- FFmpeg do PATH ---
        if platform.system().lower().startswith("win"):
            try:
                os.add_dll_directory(str(Config.FFMPEG_DIR))
            except Exception:
                # Nie przerywamy działania, jeśli nie możemy dodać katalogu DLL
                pass
        if str(Config.FFMPEG_DIR) not in os.environ.get("PATH", "") and Config.FFMPEG_DIR.exists():
            os.environ["PATH"] = str(Config.FFMPEG_DIR) + os.pathsep + os.environ.get("PATH", "")

        # --- Globalna precyzja float32 matmul ---
        try:
            torch.set_float32_matmul_precision("medium")
        except Exception:
            # starsze PyTorch/środowiska mogą nie wspierać — pomijamy
            pass

        # --- Wybór urządzenia ---
        if Config.FORCE_CPU:
            Config.DEVICE = torch.device("cpu")
            Config.DEVICE_FRIENDLY_NAME = "CPU"
        elif torch.cuda.is_available():
            Config.DEVICE = torch.device("cuda:0")
            try:
                Config.DEVICE_FRIENDLY_NAME = torch.cuda.get_device_name(0)
            except Exception:
                Config.DEVICE_FRIENDLY_NAME = "CUDA:0"
        else:
            Config.DEVICE = torch.device("cpu")
            Config.DEVICE_FRIENDLY_NAME = "CPU"

        # --- DTYPE + TF32 ---
        if Config.DEVICE.type == "cuda":
            # Preferuj bfloat16, jeśli wspierane przez architekturę
            use_bf16 = False
            try:
                if hasattr(torch.cuda, "is_bf16_supported"):
                    use_bf16 = bool(torch.cuda.is_bf16_supported())
            except Exception:
                use_bf16 = False

            Config.DTYPE = torch.bfloat16 if use_bf16 else torch.float16

            # TF32 on (jeśli możliwe)
            try:
                torch.backends.cuda.matmul.allow_tf32 = True
                Config.TF32_ENABLED = True
            except Exception:
                Config.TF32_ENABLED = False
        else:
            Config.DTYPE = torch.float32
            Config.TF32_ENABLED = False

        # --- Ostrzeżenie (ciche) gdy brak ffmpeg/ffprobe w PATH (nie przerywamy działania) ---
        for tool in ("ffmpeg", "ffprobe"):
            if shutil.which(tool) is None:
                # Aplikacja może to zalogować w innym miejscu — tutaj bez wyjątku.
                pass
