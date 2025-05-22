from pathlib import Path
import shutil

from core.config import Config
from core.audio_extractor import AudioExtractor

class FileManager:
    @staticmethod
    def copy_to_input(source: str) -> Path:
        src = Path(source)
        dst = Config.INPUT_DIR / src.name
        if src.resolve() == dst.resolve():
            return dst
        shutil.copy2(src, dst)
        return dst

    @staticmethod
    def copy_audio_only(source: str, log=print) -> Path:
        src = Path(source)
        temp_name = src.stem + ".wav"
        dst = Config.INPUT_DIR / temp_name

        if src.suffix.lower() in Config.VIDEO_EXT:
            AudioExtractor.extract_audio(src, dst, log=log)
        elif src.suffix.lower() in Config.AUDIO_EXT:
            shutil.copy2(src, dst)
        else:
            raise ValueError(f"Nieobsługiwany format pliku: {src.suffix}")

        return dst

    @staticmethod
    def should_skip_local(path: Path, log=print) -> bool:
        output_dir = Config.OUTPUT_DIR / path.stem
        if output_dir.exists():
            log(f"⏭️ Transkrypcja dla pliku '{path.name}' już istnieje — pomijam.")
            return True
        return False

    @staticmethod
    def filter_media(paths: list[Path]) -> list[Path]:
        return [p for p in paths if p.suffix.lower() in Config.AUDIO_EXT + Config.VIDEO_EXT]
