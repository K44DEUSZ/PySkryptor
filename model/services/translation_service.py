# model/services/translation_service.py
from __future__ import annotations

from typing import Any, Callable, Optional

from model.config.app_config import AppConfig as Config
from model.io.text import TextPostprocessor


LogFn = Callable[[str], None]


class TranslationService:
    """Lazy text translation pipeline wrapper.

    The translation model name is taken from engine.translation_model_name.
    This service keeps a single cached pipeline instance per model.
    """

    def __init__(self) -> None:
        self._pipe: Optional[Any] = None
        self._model_name: str = ""

    def is_ready(self) -> bool:
        return self._pipe is not None

    def reset(self) -> None:
        self._pipe = None
        self._model_name = ""

    def ensure_loaded(self, *, log: Optional[LogFn] = None) -> bool:
        cfg = Config.engine_settings()
        model_name = str(cfg.get("translation_model_name", "") or "").strip()
        if not model_name:
            if log:
                log("translation: model not configured")
            self.reset()
            return False

        if self._pipe is not None and self._model_name == model_name:
            return True

        try:
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer, pipeline  # lazy import

            tok = AutoTokenizer.from_pretrained(model_name, local_files_only=False)
            mdl = AutoModelForSeq2SeqLM.from_pretrained(model_name, local_files_only=False)
            self._pipe = pipeline("translation", model=mdl, tokenizer=tok, device=-1)
            self._model_name = model_name
            if log:
                log(f"translation: ready ({model_name})")
            return True
        except Exception as e:
            if log:
                log(f"translation: init failed: {e}")
            self.reset()
            return False

    def translate(self, text: str, *, src_lang: str = "", tgt_lang: str = "", log: Optional[LogFn] = None) -> str:
        if not text or not text.strip():
            return ""
        if not self.ensure_loaded(log=log):
            return ""

        assert self._pipe is not None
        kwargs = {}
        if src_lang:
            kwargs["src_lang"] = src_lang
        if tgt_lang:
            kwargs["tgt_lang"] = tgt_lang

        try:
            try:
                out = self._pipe(text, **kwargs)  # type: ignore[misc]
            except TypeError:
                out = self._pipe(text)  # type: ignore[misc]

            if isinstance(out, list) and out:
                cand = out[0].get("translation_text", "")
                return TextPostprocessor.clean(str(cand))
        except Exception as e:
            if log:
                log(f"translation: failed: {e}")
        return ""
