"""Command line: ``ultraai check|compare|eval``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _check(args) -> int:
    from .ultrastar.reader import read
    from .ultrastar.validator import validate

    paths = []
    for p in map(Path, args.paths):
        paths.extend(sorted(p.rglob("*.txt")) if p.is_dir() else [p])
    worst = 0
    levels = {"info": 0, "warning": 1, "error": 2}
    shown = {lv for lv, n in levels.items() if n >= levels[args.level]}
    for path in paths:
        issues = [i for i in validate(read(path), path.parent) if i.level in shown]
        if issues:
            print(path)
            for i in issues:
                print("   ", i)
            worst = max(worst, max(levels[i.level] for i in issues))
    return 1 if worst == 2 else 0


def _compare(args) -> int:
    from .eval.metrics import compare
    from .ultrastar.reader import read

    m = compare(read(args.reference), read(args.hypothesis))
    for k, v in m.items():
        print(f"{k:28} {v:.3f}" if isinstance(v, float) else f"{k:28} {v}")
    return 0


def _eval(args) -> int:
    from .eval import harness

    cases = harness.load_cases(args.library)
    if args.only:
        cases = [c for c in cases if any(o.lower() in c.name.lower() for o in args.only)]
    if args.cached_only:
        from . import config
        from .generate import is_cached
        sep = config.get("separation", default="htdemucs")  # historical default of this subcommand
        cases = [c for c in cases if c.audio and is_cached(c.audio, sep)]
    gen = harness.GENERATORS[args.generator]
    harness.run(gen, cases, Path(args.out) / args.generator)
    return 0


def _config(args) -> int:
    from . import config

    print(config.table())
    return 0


def _process(args) -> int:
    from .worker import process

    result = process(Path(args.job))
    print(result)
    return 0


def _job_status(args) -> int:
    from .worker import follow, read_status

    if args.follow:
        follow(Path(args.job))
    else:
        import json

        print(json.dumps(read_status(Path(args.job)), ensure_ascii=False))
    return 0


def _studio(args) -> int:
    from .studio.app import run

    run(port=args.port, open_window=not args.no_window)
    return 0


def _make(args) -> int:
    """Generate a song folder from an audio file and a lyrics text file."""
    import shutil

    from .audio import separate
    from .generate import generate
    from .lyrics import language_code, prepare
    from .ultrastar.writer import write

    audio = Path(args.audio).resolve()
    raw = Path(args.lyrics).read_text(encoding="utf-8")
    lang = language_code(args.language)
    meta = {"ARTIST": args.artist, "TITLE": args.title,
            "LANGUAGE": {"en": "English", "de": "German", "el": "Greek"}.get(lang, args.language or "")}
    name = f"{args.artist} - {args.title}"
    out = Path(args.out) / name
    out.mkdir(parents=True, exist_ok=True)
    target_audio = out / f"{name}{audio.suffix}"
    if not target_audio.exists():
        shutil.copy2(audio, target_audio)
    vocals, instr = separate(audio, args.separation)
    meta["VOCALS"] = f"{name} [VOC].wav"
    meta["INSTRUMENTAL"] = f"{name} [INSTR].wav"
    shutil.copy2(vocals, out / meta["VOCALS"])
    shutil.copy2(instr, out / meta["INSTRUMENTAL"])
    song, _ = generate(audio, prepare(raw, lang), meta, separation=args.separation)
    song.set("MP3", target_audio.name)
    song.set("AUDIO", target_audio.name)
    write(song, out / f"{name}.txt")
    print(out / f"{name}.txt")
    return 0


def _batch(args) -> int:
    from .batch import run_batch

    report = run_batch(Path(args.input), Path(args.output), separation=args.separation,
                       lead=None if args.lead == "none" else args.lead,
                       greek_script=args.greek_script, stems=not args.no_stems, force=args.force)
    print(f"report: {report}")
    return 0


def _review(args) -> int:
    from .review import serve

    serve(Path(args.folder), port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ultraai")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="validate UltraStar txt files")
    c.add_argument("paths", nargs="+")
    c.add_argument("--level", choices=["info", "warning", "error"], default="warning")
    c.set_defaults(func=_check)

    c = sub.add_parser("compare", help="score a generated txt against a reference txt")
    c.add_argument("reference")
    c.add_argument("hypothesis")
    c.set_defaults(func=_compare)

    c = sub.add_parser("eval", help="regenerate library songs and score them")
    c.add_argument("library", nargs="?", default="songs")
    c.add_argument("--generator", default="naive")
    c.add_argument("--out", default="out/eval")
    c.add_argument("--only", nargs="*", help="substring filter on song folder names")
    c.add_argument("--cached-only", action="store_true",
                   help="only songs whose vocals/emissions are already cached (fast dev loop)")
    c.set_defaults(func=_eval)

    c = sub.add_parser("config", help="list all ULTRAAI_* options, their defaults and doc strings")
    c.add_argument("cmd", nargs="?", default="list", choices=["list"])
    c.set_defaults(func=_config)

    c = sub.add_parser("process", help="process one Studio job folder (job.json -> out/)")
    c.add_argument("job")
    c.set_defaults(func=_process)

    c = sub.add_parser("job-status", help="print a job's status (--follow: until it finishes)")
    c.add_argument("job")
    c.add_argument("--follow", action="store_true")
    c.set_defaults(func=_job_status)

    c = sub.add_parser("studio", help="start UltraAI Studio (library, queue, editor)")
    c.add_argument("--port", type=int, default=8766)
    c.add_argument("--no-window", action="store_true", help="do not open a window/browser")
    c.set_defaults(func=_studio)

    c = sub.add_parser("make", help="create a song folder from audio + lyrics text")
    c.add_argument("audio")
    c.add_argument("lyrics", help="plain text file, one lyric line per line")
    c.add_argument("--artist", required=True)
    c.add_argument("--title", required=True)
    c.add_argument("--language", default="English")
    c.add_argument("--separation", default="htdemucs")
    c.add_argument("--out", default="out/songs")
    c.set_defaults(func=_make)

    c = sub.add_parser("batch", help="convert a folder of audio files into USDX song folders")
    c.add_argument("input", help="folder with audio files (searched recursively)")
    c.add_argument("output", help="where the song folders are created")
    c.add_argument("--separation", default="melband", help="melband (best, slow) | htdemucs (fast)")
    c.add_argument("--lead", default="kara2", help="lead-vocal model, or 'none'")
    c.add_argument("--greek-script", action="store_true", help="keep Greek letters instead of Greeklish")
    c.add_argument("--no-stems", action="store_true", help="don't write [VOC]/[INSTR] files")
    c.add_argument("--force", action="store_true", help="redo songs already in report.csv")
    c.set_defaults(func=_batch)

    c = sub.add_parser("review", help="web page to listen to generated songs and nudge lines")
    c.add_argument("folder", help="output folder of 'ultraai batch'")
    c.add_argument("--port", type=int, default=8765)
    c.set_defaults(func=_review)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
