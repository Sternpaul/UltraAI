# Pipeline: audio → .txt

```
audio file
 ├─ read tags (mutagen)                    artist/title/cover/embedded lyrics
 ├─ find lyrics: sidecar .lrc/.txt → LRCLIB → embedded tags → YouTube captions (last resort,
 │    shown with a warning badge); numerals spelled out (22 → twenty-two)
 ├─ separate() → vocals.<sep>.wav / instrumental.<sep>.wav      (cached per audio file)
 ├─ analyse(): mono 16k → MMS_FA CTC emissions + pitch track      (cached)
 ├─ transcribe(): Whisper → words with times                      (cached, per model+lang)
 ├─ structure.match(): sung order, repeats/skips (DP over ASR words × lyric lines)
 ├─ align(): MMS_FA Viterbi, free + anchored to LRC times, Whisper time prior (ULTRAAI_ASR_PRIOR)
 ├─ syllables → notes (melisma split, golden/freestyle), drop unsung vocables, split lines,
 │    harmonize repeats
 ├─ issues: risk model + double-check models (pitch/separation disagreement → warnings)
 ├─ tempo: beat detection → robust grid choice → #BPM/#GAP (+ #VIDEOGAP if a video exists)
 └─ write .txt (+ #PREVIEWSTART at the chorus, optional .lrc/.ass) + .ultraai.json sidecar
```

Cache layout (`ULTRAAI_CACHE`, default `~/.cache/ultraai`): `work/<sha1(audio)[:16]>/` holds
the per-song intermediates (vocals, emissions, pitch, ASR, beats, onsets), `models/` the
downloaded model files, `lrclib/` LRCLIB responses (30-day TTL), `yt/` YouTube caption
responses. The cache is keyed by the audio file's sha1, so it transfers between machines
together with identical audio files.

Quality signals that end up as review issues or warnings: the HGB risk model over per-word
features, pitch double-check disagreement (`ULTRAAI_PITCH_CHECK`, rmvpe by default), separation
double-checks, lyric/ASR word mismatch, and the song-level "lyrics may not match the singing"
rule (heard < 40 % or ≥ 50 % of lines uncertain).
