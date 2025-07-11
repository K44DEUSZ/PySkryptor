from typing import Callable

class YtdlpLogger:
    def __init__(self, log: Callable[[str], None]):
        self.log = log

    def debug(self, msg):
        pass

    def info(self, msg):
        if '[generic]' not in msg.lower():
            self.log(f"ℹ️ {msg}")

    def warning(self, msg):
        if '[generic]' not in msg.lower():
            self.log(f"⚠️ {msg}")

    def error(self, msg):
        if 'unsupported url' not in msg.lower():
            self.log(f"❌ Błąd: {msg}")
