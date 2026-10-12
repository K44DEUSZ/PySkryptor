# PySkryptor

[Wersja polska](README.md)

PySkryptor turns speech from recordings into text and can translate it. You can work with a file, transcribe live audio or download material from a URL. The application has a desktop interface and is built for Windows.

Transcription and translation use models running locally. Results can be saved as plain text or SRT subtitles. `yt-dlp` handles downloads, while FFmpeg prepares the audio.

## Running from source

This repository contains the application code, but not model weights or FFmpeg executables. You need local copies before running it. Installing the Python dependencies alone is not enough.

On Windows, from the repository directory:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app/main.py
```

`requirements.txt` specifies PyTorch with CUDA 12.4. Choose compatible hardware and an environment for your setup; the application does not assume that every computer has a CUDA capable GPU.

Place models in `models/`, each in its own directory containing its configuration and weights. The code recognizes Whisper transcription models and M2M100 translation models. Put `ffmpeg.exe` and `ffprobe.exe` in `bin/ffmpeg/`. Downloads may also require Deno at `bin/deno/deno.exe`, depending on the source. Models and tools have their own licenses; check them when selecting files.

## Finding your way around

- `app/model/transcription/`: transcription, audio preparation and output files.
- `app/model/translation/`: translation.
- `app/model/download/`: downloads.
- `app/model/engines/`: starting and managing models.
- `app/view/` and `app/controller/`: interface and user actions.
- `assets/`: application resources, including interface translations.

Settings, downloads, results and logs go into `userdata/`, outside the Git history.

## Building the application

Install `requirements-dev.txt`, then run `build_onedir.bat`. The script uses PyInstaller to create `dist/PySkryptor/`, containing the application and a separate `AIModelHost.exe` process. Keep `assets/`, `bin/`, `models/` and `userdata/` beside the EXE files; the script copies whichever of these directories are available locally.

## License

PySkryptor's code is available under [GNU GPL v3](LICENSE). Libraries, tools and models retain their own licenses. See [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt) for external components.
