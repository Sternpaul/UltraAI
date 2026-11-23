"""Show M3's decisions for one library song: sung structure vs LRC, merged times, reference line starts.

    python scripts/m3_debug.py "song name substring" [library]
"""
import os
import sys

os.environ.setdefault("ULTRAAI_SEPARATION", "htdemucs")
from ultraai.eval.harness import load_cases  # noqa: E402
from ultraai.generate import analyse, lrc_lines, _merge_lrc_times, _consensus_offset, _drop_weak_repeats  # noqa: E402
from ultraai.lrc import fetch_candidates, audio_duration  # noqa: E402
from ultraai.lyrics import language_code  # noqa: E402
from ultraai.structure import match, fill_times  # noqa: E402
from ultraai.transcribe import transcribe  # noqa: E402


def main(q, root="library"):
    case = next(c for c in load_cases(root) if q.lower() in c.name.lower())
    a = analyse(case.audio, os.environ["ULTRAAI_SEPARATION"])
    lang = language_code(case.metadata.get("LANGUAGE"))
    asr = transcribe(a.vocals.with_name(a.vocals.stem + ".mono16000.wav"), lang, wave=a.wave)
    cands = fetch_candidates(case.metadata.get("ARTIST", ""), case.metadata.get("TITLE", ""), case.audio)
    scored = []
    for k, c in enumerate(cands):
        texts, times = lrc_lines(c)
        st = match(texts, asr)
        scored.append((st.per_word, k, st, texts, times))
        print(f"cand {k}: per_word {st.per_word:.3f} lines {len(texts)} synced {times is not None} jumps {st.jumps}")
    scored.sort(key=lambda x: -x[0])
    _, k, st, texts, times = scored[0]
    if times:
        off = _consensus_offset(st.lines, times)
        print("consensus offset", off)
        if off is not None:
            st = match(texts, asr, line_times=times, offset=off)
            st.lines = _drop_weak_repeats(st.lines)
    ft = fill_times(st.lines, audio_duration(case.audio))
    mt = _merge_lrc_times(st.lines, ft, times) if times else ft
    ref = [(case.reference.beat_to_seconds(ln.start), ln.text.strip()) for ln in case.reference.lines()]
    print(f"\nchosen cand {k}; ref has {len(ref)} lines")
    print("  occ  L#   asr     lrc    merged  heard  text  || nearest ref line")
    for i, ln in enumerate(st.lines):
        lt = times[ln.line] if times else None
        near = min(ref, key=lambda r: abs(r[0] - mt[i]))
        print(f"{i:4} L{ln.line:<3} {ln.start if ln.start is not None else float('nan'):7.2f} "
              f"{lt if lt is not None else float('nan'):7.2f} {mt[i]:7.2f} {ln.matched:2}/{ln.words:<2} "
              f"{ln.text[:32]:32} || {near[0]:7.2f} {near[1][:30]}")


if __name__ == "__main__":
    main(*sys.argv[1:])
