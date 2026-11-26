"""Benchmark harness: regenerate library songs from audio + lyrics and score them.

A *generator* is any callable ``(case: SongCase) -> Song``. It may use ``case.audio``,
``case.lyrics`` and ``case.metadata`` but must NOT look at ``case.reference`` timing.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..ultrastar.model import Line, Note, Song
from ..ultrastar.reader import read
from ..ultrastar.writer import write
from .metrics import SUMMARY_KEYS, compare, format_summary


@dataclass
class SongCase:
    name: str
    folder: Path
    txt: Path
    audio: Path | None
    reference: Song
    lyrics: list[str]           # one entry per reference screen line
    metadata: dict[str, str]    # TITLE, ARTIST, LANGUAGE, YEAR, GENRE, ...


def load_cases(root: str | Path) -> list[SongCase]:
    cases = []
    for txt in sorted(Path(root).glob("*/*.txt")):
        ref = read(txt)
        audio = txt.parent / ref.audio if ref.audio else None
        meta = {k: v for k, v in ref.headers if k in
                ("TITLE", "ARTIST", "LANGUAGE", "YEAR", "GENRE", "EDITION", "COVER", "BACKGROUND")}
        cases.append(SongCase(txt.parent.name, txt.parent, txt,
                              audio if audio and audio.exists() else None,
                              ref, ref.lyrics(), meta))
    return cases


Generator = Callable[[SongCase], Song]


def naive_generator(case: SongCase) -> Song:
    """Floor baseline (cheats: uses the reference's first/last note time).

    Spreads all words evenly over the sung span, one note per word, pitch 0.
    Any real pipeline must beat this by a wide margin.
    """
    ref = case.reference
    notes = list(ref.notes())
    t0, t1 = ref.beat_to_seconds(notes[0].start), ref.beat_to_seconds(notes[-1].end)
    words = [(li, w) for li, text in enumerate(case.lyrics) for w in text.split()]
    song = Song(headers=[("VERSION", "1.0.0")] + list(case.metadata.items()) +
                [("MP3", ref.audio or ""), ("BPM", "300"), ("GAP", f"{t0 * 1000:.0f}")])
    total_beats = song.seconds_to_beat(t1)
    step = total_beats / max(1, len(words))
    lines: list[Line] = []
    for k, (li, w) in enumerate(words):
        if not lines or li != lines[-1].notes[-1].pitch_line:  # type: ignore[attr-defined]
            if lines:
                lines[-1].break_beat = lines[-1].end
            lines.append(Line())
        n = Note(":", round(k * step), max(1, round(step) - 1), 0, w + " ")
        n.pitch_line = li  # type: ignore[attr-defined]
        lines[-1].notes.append(n)
    song.voices[1] = lines
    return song


GENERATORS: dict[str, Generator] = {"naive": naive_generator}


def run(generator: Generator, cases: list[SongCase], out_dir: str | Path) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results = {}
    for case in cases:
        try:
            hyp = generator(case)
        except Exception as exc:  # keep going; report the failure
            print(f"FAIL {case.name}: {exc!r}")
            results[case.name] = {"error": repr(exc)}
            continue
        write(hyp, out / f"{case.name}.txt")
        m = compare(case.reference, hyp)
        results[case.name] = m
        print(f"{case.name[:40]:40} {format_summary(m)}")

    ok = [m for m in results.values() if "error" not in m]
    summary = {}
    for key, _ in SUMMARY_KEYS:
        vals = [m[key] for m in ok if key in m]
        if vals:
            summary[key] = statistics.mean(vals)
    print(f"\nMEAN over {len(ok)}/{len(results)} songs:\n  {format_summary(summary)}")
    (out / "results.json").write_text(json.dumps({"summary": summary, "songs": results}, indent=1))
    return summary


def m1_generator(case: SongCase, separation: str | None = None) -> Song:
    """Real pipeline: audio + the reference's lyric lines (timing hidden)."""
    import os

    from ..audio import DEFAULT_SEPARATION
    from ..generate import generate
    from ..lyrics import language_code, prepare

    if case.audio is None:
        raise FileNotFoundError("no audio")
    from .. import config
    sep = separation or config.get("separation")
    lyrics = prepare(case.lyrics, language_code(case.metadata.get("LANGUAGE")))
    song, _ = generate(case.audio, lyrics, case.metadata, separation=sep)
    return song


GENERATORS["m1"] = m1_generator


def m2_generator(case: SongCase) -> Song:
    """Realistic: lyrics AND line timing fetched from LRCLIB; nothing from the reference."""
    import os

    from ..audio import DEFAULT_SEPARATION
    from ..generate import generate_from_lrc
    from ..lrc import fetch
    from ..lyrics import language_code

    if case.audio is None:
        raise FileNotFoundError("no audio")
    lrc = fetch(case.metadata.get("ARTIST", ""), case.metadata.get("TITLE", ""), case.audio)
    if lrc is None:
        raise LookupError("no lyrics on LRCLIB")
    from ..batch import detect_language, expand_candidates, greeklish

    code, name = detect_language(lrc.texts)
    if code == "el" and name == "Greek" and "roman" in case.metadata.get("LANGUAGE", "").lower():
        for ln in lrc.lines:
            ln.text = greeklish(ln.text)
        lrc.plain = [greeklish(t) for t in lrc.plain]
    lrc = expand_candidates([lrc], code)[0]
    from .. import config
    sep = config.get("separation")
    song, info = generate_from_lrc(case.audio, lrc, language_code(case.metadata.get("LANGUAGE")),
                                   case.metadata, separation=sep)
    print(f"   [{case.name[:30]}] lrc {lrc.duration:.0f}s synced={info['lrc_synced']} "
          f"anchored={info["anchored"]} offset={info.get("offset_median", 0):+.2f}s tempo={info.get("tempo")}")
    return song


GENERATORS["m2"] = m2_generator


def m3_generator(case: SongCase) -> Song:
    """Listen first: Whisper transcript picks the lyric version and the sung structure."""
    import os

    from ..audio import DEFAULT_SEPARATION
    from ..batch import detect_language, expand_candidates, greeklish
    from ..generate import generate_sung
    from ..lrc import fetch_candidates
    from ..lyrics import language_code

    if case.audio is None:
        raise FileNotFoundError("no audio")
    cands = fetch_candidates(case.metadata.get("ARTIST", ""), case.metadata.get("TITLE", ""), case.audio)
    if not cands:
        raise LookupError("no lyrics on LRCLIB")
    roman = "roman" in case.metadata.get("LANGUAGE", "").lower()
    for c in cands:
        code, name = detect_language(c.texts)
        if roman and code == "el" and name == "Greek":
            for ln in c.lines:
                ln.text = greeklish(ln.text)
            c.plain = [greeklish(t) for t in c.plain]
    cands = expand_candidates(cands, language_code(case.metadata.get("LANGUAGE")))
    from .. import config
    sep = config.get("separation")
    song, info = generate_sung(case.audio, cands, language_code(case.metadata.get("LANGUAGE")),
                               case.metadata, separation=sep)
    song.generation_info = info  # for scripts that keep the sidecar (issues, features)
    print(f"   [{case.name[:30]}] cand {info['candidate']}/{info['candidates']} {info['structure']} "
          f"asr_cov={info['asr_coverage']:.2f} jumps={info['jumps']} anchored={info['anchored']}")
    return song


GENERATORS["m3"] = m3_generator
