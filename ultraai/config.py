"""Central registry of every ``ULTRAAI_*`` configuration option.

Options are read from the environment **at call time**, never at import time, so tests
stay order-independent and everything is discoverable:

    from . import config
    if config.get_bool("melisma"): ...
    pitch_model = config.get("pitch")

List them all with ``ultraai config list``.  The Studio passes per-song options through
``job.json``; ``worker.apply_options`` maps them onto the same environment variables.

Two options are process-level and still read once at import (documented on the option):
``cache`` (the cache root is a module-level constant) and ``background_units``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Opt:
    key: str      # name used with config.get() — the env var minus the ULTRAAI_ prefix, lowercased
    env: str      # environment variable (kept stable: scripts and the server setup use it)
    default: str
    doc: str


OPTS: list[Opt] = [
    Opt("cache", "ULTRAAI_CACHE", "", "cache directory (default: ~/.cache/ultraai); read once at import"),
    Opt("separation", "ULTRAAI_SEPARATION", "htdemucs", "default vocal separation model (audio.py)"),
    Opt("pitch", "ULTRAAI_PITCH", "swift", "pitch model: swift|rmvpe|fcpe|pesto|crepe-tiny|crepe-full|vote"),
    Opt("lead", "ULTRAAI_LEAD", "", "backing-vocal removal model applied to the vocal stem ('' = keep)"),
    Opt("notes", "ULTRAAI_NOTES", "singer", "note style: singer|easier|easiest"),
    Opt("pitch_check", "ULTRAAI_PITCH_CHECK", "rmvpe", "comma-separated extra pitch models used as double check (warnings only); '' = none. rmvpe: 78% of its warnings hit real errors, ~8 new finds per song (BENCHMARK.md)"),
    Opt("pitch2", "ULTRAAI_PITCH2", "", "legacy alias of pitch_check"),
    Opt("sep_check", "ULTRAAI_SEP_CHECK", "", "comma-separated extra separation models used as double check"),
    Opt("sep2", "ULTRAAI_SEP2", "", "legacy alias of sep_check"),
    Opt("asr_model", "ULTRAAI_ASR_MODEL", "large-v3-turbo", "faster-whisper model for the transcript (large-v3-turbo|large-v3|medium|...)"),
    Opt("asr_sep", "ULTRAAI_ASR_SEP", "", "separation whose vocals the Whisper transcript is taken from (benchmarks)"),

    Opt("check_autofix", "ULTRAAI_CHECK_AUTOFIX", "0", "auto-correct note pitch to the consensus of >=2 agreeing double-check models; only active when more than one check model is set; 0 disables"),
    Opt("melisma", "ULTRAAI_MELISMA", "1", "split held syllables into ~ notes at pitch changes"),
    Opt("minseg", "ULTRAAI_MINSEG", "0.15", "min seconds a pitch region must last to become a note (singer style)"),
    Opt("maxseg", "ULTRAAI_MAXSEG", "6", "max pitch steps inside one long wobbly syllable"),
    Opt("robust", "ULTRAAI_ROBUST", "1", "re-align untrusted stretches between trusted anchor lines"),
    Opt("golden", "ULTRAAI_GOLDEN", "jumps", "golden-note rule: jumps|legacy"),
    Opt("maxc", "ULTRAAI_MAXC", "40", "max chars per screen line before splitting"),
    Opt("maxs", "ULTRAAI_MAXS", "5.0", "max seconds per screen line before splitting"),
    Opt("harmonize", "ULTRAAI_HARMONIZE", "1", "give every repetition of a line the same notes"),
    Opt("harm_agree", "ULTRAAI_HARM_AGREE", "0.6", "share of syllables that must agree to harmonize a repeat"),
    Opt("harm_tol", "ULTRAAI_HARM_TOL", "2", "semitones within which a repeat counts as agreeing"),
    Opt("harm_min", "ULTRAAI_HARM_MIN", "1", "min syllables for a line to be grouped as a repeat"),
    Opt("harm_melisma", "ULTRAAI_HARM_MELISMA", "0", "also unify melisma shapes across repeats"),
    Opt("harm_mode", "ULTRAAI_HARM_MODE", "pool", "consensus pitch across repeats: pool|median"),
    Opt("tempo", "ULTRAAI_TEMPO", "1", "detect beats and write a tempo-locked #BPM/#GAP"),
    Opt("beat_robust", "ULTRAAI_BEAT_ROBUST", "1", "score half/double-tempo candidate grids against the onsets with a plausible-BPM prior (tempo.py); 0 = legacy steady-grid choice"),
    Opt("previewstart", "ULTRAAI_PREVIEWSTART", "1", "write #PREVIEWSTART (seconds) at the first chorus start: first note of the largest repeated line group"),
    Opt("export_lrc", "ULTRAAI_EXPORT_LRC", "0", "write a word-synced .lrc next to the song .txt (byproducts.py)"),
    Opt("export_ass", "ULTRAAI_EXPORT_ASS", "0", "write a karaoke .ass next to the song .txt (byproducts.py)"),
    Opt("repair", "ULTRAAI_REPAIR", "1", "override LRC timestamps the audio clearly contradicts"),
    Opt("repair_conf", "ULTRAAI_REPAIR_CONF", "0.15", "min alignment confidence to trust the audio over the LRC"),
    Opt("repair_shift", "ULTRAAI_REPAIR_SHIFT", "3.0", "seconds of disagreement that count as a wrong LRC timestamp"),
    Opt("m3_cand_margin", "ULTRAAI_M3_CAND_MARGIN", "0.0", "bonus for the default lyric candidate in the M3 ranking"),
    Opt("m3_timed", "ULTRAAI_M3_TIMED", "1", "re-match the transcript knowing the LRC line times"),
    Opt("m3_min_coverage", "ULTRAAI_M3_MIN_COVERAGE", "0.5", "min transcript coverage to trust the sung structure"),
    Opt("m3_min_heard", "ULTRAAI_M3_MIN_HEARD", "0.3", "min share of lyric words heard to trust the sung structure"),
    Opt("m3_times", "ULTRAAI_M3_TIMES", "hybrid", "line times for a confirmed structure: hybrid|asr|lrc"),
    Opt("m3_lrc_fill", "ULTRAAI_M3_LRC_FILL", "1", "fill line times of a sung structure from the LRC where they agree"),
    Opt("m3_trust", "ULTRAAI_M3_TRUST", "1", "anchor to LRC/transcript times even when free alignment is poor"),
    Opt("crepe", "ULTRAAI_CREPE", "tiny", "CREPE model size: tiny|full"),
    Opt("struct_drop", "ULTRAAI_STRUCT_DROP", "0", "drop weakly heard extra line repeats in the structure DP"),
    Opt("stage_timing", "ULTRAAI_STAGE_TIMING", "0", "record per-stage seconds of the pipeline into generate.STAGE_TIMES (benchmarks)"),
    Opt("onset_snap", "ULTRAAI_ONSET_SNAP", "0", "snap aligned word onsets to detected vocal onsets within the window"),
    Opt("onset_snap_win", "ULTRAAI_ONSET_SNAP_WIN", "0.08", "seconds around a word onset to look for a vocal onset to snap to"),
    Opt("keysnap", "ULTRAAI_KEYSNAP", "0", "snap final note pitches to the detected key (Krumhansl-Schmuckler profiles, pitchfit.py); 0 = off"),
    Opt("octave_range", "ULTRAAI_OCTAVE_RANGE", "", "fold final note pitches by whole octaves into this midi range 'lo,hi' (e.g. 48,72); '' = off"),
    Opt("videogap", "ULTRAAI_VIDEOGAP", "1", "write #VIDEOGAP (seconds, 2 decimals) from onset cross-correlation when a sibling video file exists (video.py)"),
    Opt("yt_subs", "ULTRAAI_YT_SUBS", "1", "use YouTube auto/manual subtitles as the lyrics source when a video id is known"),
    Opt("yt_subs_search", "ULTRAAI_YT_SUBS_SEARCH", "0", "resolve the video id by searching 'ARTIST - TITLE' when unknown (slow)"),
    Opt("yt_anchor", "ULTRAAI_YT_ANCHOR", "0", "use subtitle word times as LRC-style line anchors in the anchored alignment; default 0 — on 5 English gate songs with exact video ids this regressed timing (-4.9 onset_within_250ms, -5.7 rel-100ms), caption-chunk starts are not musical line starts"),
    Opt("cookies", "ULTRAAI_COOKIES", "", "path to a Netscape cookies.txt passed to yt-dlp via --cookies (YouTube 429 rate limits); the file is a secret: never logged, never committed"),
    Opt("yt_impersonate", "ULTRAAI_YT_IMPERSONATE", "chrome", "yt-dlp --impersonate target (e.g. chrome) for YouTube requests; lifts most 429 rate limits on subtitle downloads. Requires curl_cffi. '' disables"),
    Opt("yt_sleep_subs", "ULTRAAI_YT_SLEEP_SUBS", "60", "yt-dlp --sleep-subtitles seconds; YouTube restricts auto-subtitles for sessions younger than ~60 s. 0 disables"),
    Opt("asr_prior", "ULTRAAI_ASR_PRIOR", "1.0", "Gaussian time-prior bonus (log-prob units) for aligned words near their Whisper-heard time; 0 disables"),
    Opt("asr_prior_sigma", "ULTRAAI_ASR_PRIOR_SIGMA", "0.3", "std-dev (s) of the Gaussian Whisper time prior in the anchored Viterbi"),
    Opt("background_units", "ULTRAAI_BACKGROUND_UNITS",
        "ultraai-bench.service ultraai-library.service ultraai-after-run.service",
        "systemd user units paused while a song job runs; read once at import"),

    Opt("max_jobs", "ULTRAAI_MAX_JOBS", "1",
        "max song jobs processed in parallel on this machine (worker.py: per-audio-hash lock files + slot semaphore); "
        "1 = one job at a time, as before. Each job is a process with the full model stack in memory, "
        "so N needs roughly N x the RAM/VRAM of one job; on a cold cache two jobs can also download the same "
        "model into the shared MODEL_DIR at once — run one job first to warm it"),
]

_BY_KEY = {o.key: o for o in OPTS}


def get(key: str, default: str | None = None) -> str:
    """Current value of option ``key``: its env var if set, else the registered default.

    ``default`` overrides the registered default for call sites that historically used a
    different one (kept explicit so behavior never changes silently).
    """
    try:
        o = _BY_KEY[key]
    except KeyError:
        raise KeyError(f"unknown option {key!r}; known: {', '.join(sorted(_BY_KEY))}") from None
    v = os.environ.get(o.env)
    if v is not None:
        return v
    return o.default if default is None else default


def env_of(key: str) -> str:
    """Environment variable name of option ``key`` (worker.apply_options maps job options onto these)."""
    return _BY_KEY[key].env


def get_bool(key: str) -> bool:
    return get(key) == "1"


def get_float(key: str) -> float:
    return float(get(key))


def get_int(key: str) -> int:
    return int(get(key))


def get_list(key: str) -> list[str]:
    return [v.strip() for v in get(key).split(",") if v.strip()]


def table() -> str:
    """All options as a text table (``ultraai config list``)."""
    w_env = max(len(o.env) for o in OPTS)
    w_key = max(len(o.key) for o in OPTS)
    lines = [f"{'option':<{w_key}}  {'env':<{w_env}}  {'default':<10}  doc"]
    for o in OPTS:
        d = o.default if len(o.default) <= 10 else o.default[:9] + "…"
        lines.append(f"{o.key:<{w_key}}  {o.env:<{w_env}}  {d:<10}  {o.doc}")
    return "\n".join(lines)
