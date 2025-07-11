from pathlib import Path
import subprocess
from typing import Callable

class AudioExtractor:
    @staticmethod
    def has_audio(path: Path) -> bool:
        ffprobe = Path(__file__).resolve().parent.parent / ".ffmpeg" / "bin" / "ffprobe.exe"
        try:
            result = subprocess.run(
                [
                    str(ffprobe),
                    "-v", "błąd",
                    "-select_streams", "a",
                    "-show_entries", "stream=codec_type",
                    "-of", "default=nw=1:nk=1",
                    str(path)
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=True
            )
            return "audio" in result.stdout
        except subprocess.CalledProcessError:
            return False

    @staticmethod
    def extract_audio(video_path: Path, output_path: Path, log: Callable[[str], None] = print) -> None:
        if not AudioExtractor.has_audio(video_path):
            log(f"⏭️ Plik '{video_path.name}' nie zawiera ścieżki audio — pomijam.")
            return

        ffmpeg = Path(__file__).resolve().parent.parent / ".ffmpeg" / "bin" / "ffmpeg.exe"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        command = [
            str(ffmpeg), "-y",
            "-i", str(video_path),
            "-ac", "1",
            "-ar", "16000",
            str(output_path)
        ]

        try:
            subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            log(f"🎧 Wyodrębniono audio: {output_path.name}")
        except subprocess.SubprocessError as error:
            log(f"❌ Błąd podczas wyodrębniania audio z pliku {video_path.name}: {error}")
            raise
