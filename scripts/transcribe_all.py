"""Whisper-transcribe the cached vocals of every benchmark song (for the structure check)."""
import time

from ultraai.audio import work_dir
from ultraai.eval.harness import load_cases
from ultraai.lyrics import language_code
from ultraai.transcribe import transcribe

for c in load_cases("songs"):
    voc = work_dir(c.audio) / "vocals.htdemucs.mono16000.wav"
    if not voc.exists():
        continue
    t = time.time()
    segs = transcribe(voc, language_code(c.metadata.get("LANGUAGE")))
    print(f"{c.name[:40]:40} {len(segs):3} segments  {time.time() - t:5.0f} s", flush=True)
