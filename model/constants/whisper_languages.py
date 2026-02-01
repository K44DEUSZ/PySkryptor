# model/constants/whisper_languages.py
from __future__ import annotations

from typing import List


def whisper_language_codes() -> List[str]:
    """Return the list of language codes supported by Whisper tokenizers.

    Prefer the list shipped with HuggingFace Transformers (when available),
    fall back to a curated static list.
    """
    try:
        from transformers.models.whisper.tokenization_whisper import LANGUAGES  # type: ignore
        codes = sorted(
            {str(k).lower().replace("_", "-") for k in (LANGUAGES or {}).keys() if str(k).strip()}
        )
        if codes:
            return codes
    except Exception:
        pass

    # Fallback list (covers common Whisper language codes).
    return sorted(
        {
            "af","am","ar","as","az","ba","be","bg","bn","bo","br","bs","ca","cs","cy","da","de","el","en","es",
            "et","eu","fa","fi","fo","fr","gl","gu","ha","haw","he","hi","hr","ht","hu","hy","id","is","it","ja",
            "jw","ka","kk","km","kn","ko","la","lb","ln","lo","lt","lv","mg","mi","mk","ml","mn","mr","ms","mt",
            "my","ne","nl","nn","no","oc","pa","pl","ps","pt","ro","ru","sa","sd","si","sk","sl","sn","so","sq",
            "sr","su","sv","sw","ta","te","tg","th","tk","tl","tr","tt","uk","ur","uz","vi","yi","yo","zh"
        }
    )
