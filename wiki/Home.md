# UltraAI wiki

UltraAI turns songs into [UltraStar Deluxe](https://usdx.eu) karaoke files automatically:
lyrics, timing, line breaks and notes. The lyrics are treated as the truth (sidecar file →
LRCLIB → audio tags → YouTube captions as a flagged last resort); the audio decides *when*
each syllable is sung and at which pitch. A risk model plus pitch double-checks mark the
places where the machine is unsure, and **UltraAI Studio** takes you straight there to fix
them. Inspired by [UltraSinger](https://github.com/rakuri255/UltraSinger).

- Install the Studio from the [latest release](https://github.com/Sternpaul/UltraAI/releases/latest) — see [Studio](Studio)
- Or run the pipeline from the [command line](CLI) — [Configuration](Configuration) lists every `ULTRAAI_*` setting
- How it works, stage by stage: [Pipeline](Pipeline)
- Which models score best and why the defaults are what they are: [Benchmarks](Benchmarks)
- When something goes wrong: [Troubleshooting](Troubleshooting)

Full source and quick start: [github.com/Sternpaul/UltraAI](https://github.com/Sternpaul/UltraAI)
(LICENSE: MIT).
