class TranscriptionProcessor:
    @staticmethod
    def clean(text: str) -> str:
        return text.replace(". ", ".\n").replace("? ", "?\n").replace("! ", "!\n")
