"""Sung-structure summary for all benchmark songs that have a cached transcript."""
from ultraai.audio import work_dir
from ultraai.eval.harness import load_cases
from ultraai.generate import lrc_lines
from ultraai.lrc import fetch_candidates
from ultraai.lyrics import language_code
from ultraai.structure import match
from ultraai.transcribe import DEFAULT_MODEL, transcribe

for c in load_cases("songs"):
    lang = language_code(c.metadata.get("LANGUAGE"))
    voc = work_dir(c.audio) / "vocals.htdemucs.mono16000.wav"
    if not voc.with_name(f"{voc.stem}.asr-{DEFAULT_MODEL}-{lang}.json").exists():
        continue
    asr = transcribe(voc, lang)
    cands = fetch_candidates(c.metadata.get("ARTIST", ""), c.metadata.get("TITLE", ""), c.audio)
    res = []
    for k, cand in enumerate(cands):
        texts, _ = lrc_lines(cand)
        st = match(texts, asr)
        res.append((st.per_word, k, st, len(texts), cand.synced))
    res.sort(key=lambda r: -r[0])
    pw, k, st, n, synced = res[0]
    order = [ln.line for ln in st.lines]
    print(f"{c.name[:34]:34} asr {st.asr_words:4}w  cand {k}/{len(cands)} {'S' if synced else 'P'}  "
          f"score {pw:5.2f} (first {[r[0] for r in res if r[1]==0][0]:5.2f})  cov {st.coverage:4.0%}  "
          f"jumps {st.jumps}  lines {len(st.lines)}/{n}  {'same' if order == list(range(n)) else 'CHANGED'}")
