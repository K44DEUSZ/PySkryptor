from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


class Translator:
    _instance: Optional["Translator"] = None

    def __init__(self, mapping: Dict[str, str]) -> None:
        self._m = mapping

    @classmethod
    def load(cls, locales_dir: Path, lang: str) -> "Translator":
        p = (locales_dir / f"{lang}.json").resolve()
        mapping: Dict[str, str] = {}
        if p.exists():
            try:
                mapping = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                mapping = {}
        cls._instance = Translator(mapping)
        return cls._instance

    @classmethod
    def current(cls) -> "Translator":
        if cls._instance is None:
            cls._instance = Translator({})
        return cls._instance

    def get(self, key: str, default: Optional[str] = None, **kwargs: Any) -> str:
        text = self._m.get(key, default if default is not None else key)
        if kwargs:
            try:
                text = text.format(**kwargs)
            except Exception:
                pass
        return text


def tr(key: str, default: Optional[str] = None, **kwargs: Any) -> str:
    return Translator.current().get(key, default, **kwargs)
