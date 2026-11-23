"""Show the sung script (lyrics vs Whisper transcript) for benchmark songs.

Usage: python scripts/structure_check.py "Song name"
"""
import sys

from ultraai.audio import work_dir
from ultraai.eval.harness import load_cases
from ultraai.generate import lrc_lines
from ultraai.lrc import fetch
from ultraai.lyrics import language_code
from ultraai.structure import describe, match
from ultraai.transcribe import transcribe

name = sys.argv[1]
c = next(c for c in load_cases("songs") if c.name == name)
lang = language_code(c.metadata.get("LANGUAGE"))
asr = transcribe(work_dir(c.audio) / "vocals.htdemucs.mono16000.wav", lang)
lrc = fetch(c.metadata.get("ARTIST", ""), c.metadata.get("TITLE", ""), c.audio)
texts, times = lrc_lines(lrc)
st = match(texts, asr)
print(describe(st))
