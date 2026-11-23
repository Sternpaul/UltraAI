"""Speech recognition on the separated vocals (Whisper via faster-whisper), cached.

Used to check *what is actually sung* (repeats, missing or extra lines) before trusting
downloaded lyrics.  The transcript's spelling is not trusted; its structure and rough
timing are.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "large-v3-turbo"


@dataclass
class AsrWord:
    start: float
    end: float
    text: str
    prob: float


@dataclass
class AsrSegment:
    start: float
    end: float
    text: str
    words: list[AsrWord]


_models: dict[str, object] = {}


def _model(name: str):
    if name not in _models:
        from faster_whisper import WhisperModel

        _models[name] = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=4)
    return _models[name]


def transcribe(vocals_16k: Path, language: str | None, model: str = DEFAULT_MODEL,
               wave=None) -> list[AsrSegment]:
    """Transcribe 16 kHz mono vocals (file, or ``wave`` samples); result cached next to the file."""
    if language in (None, "", "xx"):
        language = None
    cache = vocals_16k.with_name(f"{vocals_16k.stem}.asr-{model}-{language or 'auto'}.json")
    if cache.exists():
        raw = json.loads(cache.read_text(encoding="utf-8"))
    else:
        import numpy as np

        if wave is None:
            import soundfile as sf

            wave, sr = sf.read(str(vocals_16k), dtype="float32")
            assert sr == 16000, sr
        wav = np.asarray(wave, dtype=np.float32)
        # pass samples directly: faster-whisper's own decoder breaks with newer PyAV
        segs, _ = _model(model).transcribe(
            wav, language=language, word_timestamps=True, vad_filter=True,
            condition_on_previous_text=False, beam_size=5)
        raw = [{"start": s.start, "end": s.end, "text": s.text.strip(),
                "words": [[w.start, w.end, w.word.strip(), w.probability] for w in (s.words or [])]}
               for s in segs]
        cache.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return [AsrSegment(s["start"], s["end"], s["text"], [AsrWord(*w) for w in s["words"]]) for s in raw]
