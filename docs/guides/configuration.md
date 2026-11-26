# Configuration

Every setting is an environment variable registered in `ultraai/config.py`.
Print the live table with:

```bash
python -c "from ultraai import config; print(config.table())"
```

Defaults as of the current main:

```
option            env                       default     doc
cache             ULTRAAI_CACHE                         cache directory (default: ~/.cache/ultraai); read once at import
separation        ULTRAAI_SEPARATION        htdemucs    default vocal separation model (audio.py)
pitch             ULTRAAI_PITCH             swift       pitch model: swift|rmvpe|fcpe|pesto|crepe-tiny|crepe-full|vote
lead              ULTRAAI_LEAD                          backing-vocal removal model applied to the vocal stem ('' = keep)
notes             ULTRAAI_NOTES             singer      note style: singer|easier|easiest
pitch_check       ULTRAAI_PITCH_CHECK       rmvpe       comma-separated extra pitch models used as double check (warnings only); '' = none. rmvpe: 78% of its warnings hit real errors, ~8 new finds per song (BENCHMARK.md)
pitch2            ULTRAAI_PITCH2                        legacy alias of pitch_check
sep_check         ULTRAAI_SEP_CHECK                     comma-separated extra separation models used as double check
sep2              ULTRAAI_SEP2                          legacy alias of sep_check
asr_model         ULTRAAI_ASR_MODEL         large-v3-…  faster-whisper model for the transcript (large-v3-turbo|large-v3|medium|...)
asr_sep           ULTRAAI_ASR_SEP                       separation whose vocals the Whisper transcript is taken from (benchmarks)
check_autofix     ULTRAAI_CHECK_AUTOFIX     0           auto-correct note pitch to the consensus of >=2 agreeing double-check models; only active when more than one check model is set; 0 disables
melisma           ULTRAAI_MELISMA           1           split held syllables into ~ notes at pitch changes
minseg            ULTRAAI_MINSEG            0.15        min seconds a pitch region must last to become a note (singer style)
maxseg            ULTRAAI_MAXSEG            6           max pitch steps inside one long wobbly syllable
robust            ULTRAAI_ROBUST            1           re-align untrusted stretches between trusted anchor lines
golden            ULTRAAI_GOLDEN            jumps       golden-note rule: jumps|legacy
maxc              ULTRAAI_MAXC              40          max chars per screen line before splitting
maxs              ULTRAAI_MAXS              5.0         max seconds per screen line before splitting
harmonize         ULTRAAI_HARMONIZE         1           give every repetition of a line the same notes
harm_agree        ULTRAAI_HARM_AGREE        0.6         share of syllables that must agree to harmonize a repeat
harm_tol          ULTRAAI_HARM_TOL          2           semitones within which a repeat counts as agreeing
harm_min          ULTRAAI_HARM_MIN          1           min syllables for a line to be grouped as a repeat
harm_melisma      ULTRAAI_HARM_MELISMA      0           also unify melisma shapes across repeats
harm_mode         ULTRAAI_HARM_MODE         pool        consensus pitch across repeats: pool|median
tempo             ULTRAAI_TEMPO             1           detect beats and write a tempo-locked #BPM/#GAP
beat_robust       ULTRAAI_BEAT_ROBUST       1           score half/double-tempo candidate grids against the onsets with a plausible-BPM prior (tempo.py); 0 = legacy steady-grid choice
previewstart      ULTRAAI_PREVIEWSTART      1           write #PREVIEWSTART (seconds) at the first chorus start: first note of the largest repeated line group
export_lrc        ULTRAAI_EXPORT_LRC        0           write a word-synced .lrc next to the song .txt (byproducts.py)
export_ass        ULTRAAI_EXPORT_ASS        0           write a karaoke .ass next to the song .txt (byproducts.py)
repair            ULTRAAI_REPAIR            1           override LRC timestamps the audio clearly contradicts
repair_conf       ULTRAAI_REPAIR_CONF       0.15        min alignment confidence to trust the audio over the LRC
repair_shift      ULTRAAI_REPAIR_SHIFT      3.0         seconds of disagreement that count as a wrong LRC timestamp
m3_cand_margin    ULTRAAI_M3_CAND_MARGIN    0.0         bonus for the default lyric candidate in the M3 ranking
m3_timed          ULTRAAI_M3_TIMED          1           re-match the transcript knowing the LRC line times
m3_min_coverage   ULTRAAI_M3_MIN_COVERAGE   0.5         min transcript coverage to trust the sung structure
m3_min_heard      ULTRAAI_M3_MIN_HEARD      0.3         min share of lyric words heard to trust the sung structure
m3_times          ULTRAAI_M3_TIMES          hybrid      line times for a confirmed structure: hybrid|asr|lrc
m3_lrc_fill       ULTRAAI_M3_LRC_FILL       1           fill line times of a sung structure from the LRC where they agree
m3_trust          ULTRAAI_M3_TRUST          1           anchor to LRC/transcript times even when free alignment is poor
crepe             ULTRAAI_CREPE             tiny        CREPE model size: tiny|full
struct_drop       ULTRAAI_STRUCT_DROP       0           drop weakly heard extra line repeats in the structure DP
stage_timing      ULTRAAI_STAGE_TIMING      0           record per-stage seconds of the pipeline into generate.STAGE_TIMES (benchmarks)
onset_snap        ULTRAAI_ONSET_SNAP        0           snap aligned word onsets to detected vocal onsets within the window
onset_snap_win    ULTRAAI_ONSET_SNAP_WIN    0.08        seconds around a word onset to look for a vocal onset to snap to
keysnap           ULTRAAI_KEYSNAP           0           snap final note pitches to the detected key (Krumhansl-Schmuckler profiles, pitchfit.py); 0 = off
octave_range      ULTRAAI_OCTAVE_RANGE                  fold final note pitches by whole octaves into this midi range 'lo,hi' (e.g. 48,72); '' = off
videogap          ULTRAAI_VIDEOGAP          1           write #VIDEOGAP (seconds, 2 decimals) from onset cross-correlation when a sibling video file exists (video.py)
yt_subs           ULTRAAI_YT_SUBS           1           use YouTube auto/manual subtitles as the lyrics source when a video id is known
yt_subs_search    ULTRAAI_YT_SUBS_SEARCH    0           resolve the video id by searching 'ARTIST - TITLE' when unknown (slow)
yt_anchor         ULTRAAI_YT_ANCHOR         0           use subtitle word times as LRC-style line anchors in the anchored alignment; default 0 — on 5 English gate songs with exact video ids this regressed timing (-4.9 onset_within_250ms, -5.7 rel-100ms), caption-chunk starts are not musical line starts
cookies           ULTRAAI_COOKIES                       path to a Netscape cookies.txt passed to yt-dlp via --cookies (YouTube 429 rate limits); the file is a secret: never logged, never committed
yt_impersonate    ULTRAAI_YT_IMPERSONATE    chrome      yt-dlp --impersonate target (e.g. chrome) for YouTube requests; lifts most 429 rate limits on subtitle downloads. Requires curl_cffi. '' disables
yt_sleep_subs     ULTRAAI_YT_SLEEP_SUBS     60          yt-dlp --sleep-subtitles seconds; YouTube restricts auto-subtitles for sessions younger than ~60 s. 0 disables
asr_prior         ULTRAAI_ASR_PRIOR         1.0         Gaussian time-prior bonus (log-prob units) for aligned words near their Whisper-heard time; 0 disables
asr_prior_sigma   ULTRAAI_ASR_PRIOR_SIGMA   0.3         std-dev (s) of the Gaussian Whisper time prior in the anchored Viterbi
background_units  ULTRAAI_BACKGROUND_UNITS  ultraai-b…  systemd user units paused while a song job runs; read once at import
```

Notes:
- `ULTRAAI_COOKIES` is a path to a Netscape `cookies.txt`; the file itself is a secret and is never logged, uploaded or committed.
- `ULTRAAI_CACHE` is read once at import; set it before starting the process.
- `ULTRAAI_MAX_JOBS` > 1 lets the worker process several different songs in parallel (per-audio-hash locks).
