# PySkryptor

[Wersja polska](README.md)

PySkryptor transcribes and translates recordings, supports live transcription and translation, and downloads material from URLs. It runs on Windows and processes speech locally. The interface is available in Polish and English.

File exports include TXT, timestamped TXT, SRT, VTT, JSON, CSV and Markdown. Live exports support TXT, Markdown, JSON and CSV. Live sessions do not currently retain segment times and cannot produce timed subtitle exports.

## Running from source

You need Python 3.12, application dependencies, model weights and FFmpeg. The repository does not contain binaries or weights.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-cpu.txt
.\.venv\Scripts\python.exe app/main.py
```

Use `requirements-cuda.txt` instead of `requirements-cpu.txt` for a NVIDIA environment. The current stack uses PyQt6 6.11.0, Qt 6.11.2, Transformers 5.19.0 and PyTorch 2.14.1, with CPU or CUDA 13.2 builds. Model loading is local and does not execute custom model code.

Default models are `openai/whisper-large-v3-turbo` and `facebook/m2m100_418M`. Place their configuration, tokenizer and weights in `models/whisper-large-v3-turbo/` and `models/m2m100_418M/`. Alternatively, choose “Add from folder” in settings. Supported families are Whisper and M2M100, with safetensors, sharded weights or modern PyTorch `.bin` archives. Additional families need adapters.

Importing stores a folder reference without copying weights. File validation does not establish inference readiness. Saving settings runs a short inference probe; a failed change restores the previous working selection. An active transcription task or live session must finish before changing models.

Place `ffmpeg.exe` and `ffprobe.exe` in `bin/ffmpeg/`. The `yt-dlp[default,deno]` group supplies download dependencies and EJS. For source runs, copy Deno from the Python environment to `bin/deno/deno.exe`; the build does this automatically. Automatic remote EJS component downloads are disabled.

## Structure

- `app/bootstrap.py`: application startup and composition.
- `app/features/`: transcription, translation, downloads, sources, settings and transcript exports.
- `app/runtime/`: model catalog, AI processes, contracts and CPU/NVIDIA pack selection.
- `app/application/`: coordinators and tasks outside the GUI thread.
- `app/platform/`: microphone capture, instance ownership, tray behavior and installer integration.
- `app/ui/`: panels, components, icons and status presentation.
- `app/core/`: shared configuration, data and utilities.

This layout replaces the previous MVC directories. Large panels and global configuration still need gradual simplification. Source indentation is two spaces, enforced by Ruff, EditorConfig and a block indentation test.

## Window behavior and data

One instance runs per Windows profile. A second launch restores the existing window. Minimize-to-tray and background operation after closing the window are independent settings. Full shutdown requires confirmation; hiding preserves tasks and live capture. Without a tray, closing shows the shutdown dialog.

Source runs store data in `userdata/`. Packaged applications use `%LOCALAPPDATA%/PySkryptor`. A `portable.flag` beside the EXE enables local `userdata/`. Builds do not copy the developer's settings, results or imported models.

## Building

Build GUI and inference packs in separate Python 3.12 environments. Install `requirements.txt` for GUI, `requirements-cpu.txt` for CPU and optionally `requirements-cuda.txt` for NVIDIA. Each build environment also needs PyInstaller 6.19.0 and pyinstaller-hooks-contrib 2026.1.

```powershell
.\tools\build_release.ps1 -GuiPython .venv\Scripts\python.exe `
  -CpuPython .venv-cpu\Scripts\python.exe `
  -CudaPython .venv-cuda\Scripts\python.exe
```

Omit `-CudaPython` for a CPU-only build. Output is `dist/PySkryptor/`, with separate GUI libraries, `runtimes/cpu/` and optional `runtimes/cuda/`. The script collects license files from the PyInstaller analyses. Choose a new output directory to avoid mixing versions.

DLL discovery is restricted to the selected Python environment and Windows. Native files resolved from other tools fail the build. This prevents incompatible libraries, such as a different ICU build, from replacing the system library required by Qt.

Automatic mode prefers the installed NVIDIA pack. A startup or preparation failure triggers a separate CPU process. Readiness requires actual model computation. CUDA 13.2 targets Turing and newer architectures; older NVIDIA GPUs and AMD/Intel GPUs currently use CPU. The installer does not change system drivers.

The Inno Setup definition is `installer/PySkryptor.iss`. Compile an assembled directory with:

```powershell
ISCC.exe /DReleaseRoot="C:\absolute\path\to\PySkryptor" installer/PySkryptor.iss
```

Add `/DIncludeCuda` to offer the optional NVIDIA component. Installation is per user. Updates and uninstall require closing the application; user data remains outside the program directory. The installer definition still needs installation, upgrade and uninstall tests on clean Windows.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m ruff check app tests tools PySkryptor.spec AIModelHost.spec
.\.venv\Scripts\python.exe -m pytest -q
```

Model checks require weights and inference inputs. A GUI test or successful library import does not prove hardware compatibility. Local integration checks cover CPU and RTX 5080 Laptop, synthetic speech, live input and timestamps.

With the application closed, also check the actual executable:

```powershell
.\.venv\Scripts\python.exe tools/check_release.py --root dist/PySkryptor --require-models
```

This check uses the current profile, creates a native Windows main window and verifies application shutdown. `--require-models` requires both configured models to be ready; omit it to check GUI startup without weights. Diagnostic mode skips the welcome dialog and ends its own run automatically. Normal launches still require confirmation for full shutdown.

## Licensing and downloads

PySkryptor uses GNU GPL v3. External components retain their own terms. `THIRD_PARTY_NOTICES.txt` describes them; assembled builds contain license texts and inventories in `licenses/`.

Public distribution requires final review of native libraries, corresponding FFmpeg sources, Qt/NVIDIA components and any offered model weights. Imported models are not automatically bundled.

Downloading requires appropriate rights to the material and review of the source service's terms. Local HTTP/HLS tests verify formats and audio-track selection. They do not establish permission to download every YouTube video or confirm that every source works.
