# Troubleshooting

## YouTube 429 "Too Many Requests" when downloading audio or captions

YouTube rate-limits datacenter/plain clients. Fixes, in order:

1. Set **Settings → YouTube cookies** to a `cookies.txt` exported from your browser
   (logged-in). The app passes only the path to yt-dlp (`--cookies`); the file is never read
   into logs or uploaded. Keep it secret — anyone with it is logged in as you.
2. The app already uses `--impersonate chrome` + `--sleep-subtitles 60` (`ULTRAAI_YT_IMPERSONATE`,
   `ULTRAAI_YT_SLEEP_SUBS`), which lifts most 429s on its own.
3. If it still 429s, wait a few minutes; the worker retries with backoff. For bulk caption
   fetching, expect some videos to have no captions at all — that is normal, not an error.

## No lyrics found

Chain: sidecar `.lrc`/`.txt` next to the audio → LRCLIB → lyrics embedded in the audio tags →
YouTube captions (last resort, warning badge). If LRCLIB misses, paste the lyrics in the app
(*Add lyrics*, prefilled with what speech recognition heard) or drop a `.lrc` next to the
audio. Title/artist spelling matters — fix them via the ✎ button.

## Processing fails / job stuck

- Check the *Log* page (or `~/.cache/ultraai/worker.lock` on the server — delete a stale lock
  if a job was killed hard).
- ffmpeg missing on the server? `apt install ffmpeg`.
- Corrupt cache entries are quarantined/re-fetched automatically; to force a full re-analysis
  of one song, delete its `~/.cache/ultraai/work/<hash>/` folder.

## Cache transfer between machines

The cache is keyed by the sha1 of the audio file: copy `~/.cache/ultraai` (or rsync `work/`,
`lrclib/`, `yt/`, `models/`) to a machine that has the identical audio files and every step
becomes a cache hit. Never commit the cache or the benchmark audio to git.

## Validator warnings in the editor

Shown at the exact song time with a jump button, e.g. "note at 2:03.5 overlaps the previous
note (ends 2:03.8) — shorten the previous note or move this one later". Common codes: `overlap`,
`order`, zero-length notes, `break` (line break missing/inside the line/after the next line),
`long-line`. Fix by editing the notes, or dismiss the issue with ✓ OK if it is intentional.

## Studio won't start / 403

The app binds 127.0.0.1 only and checks the `Host` header; open it via the app (or
`http://127.0.0.1:<port>`). Every non-GET request needs the per-install token from
`<config dir>/token.txt` (the web UI injects it automatically).
