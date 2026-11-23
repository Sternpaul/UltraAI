"""How well does our syllabifier reproduce hand-made splits?  Per language, over a song folder.

Usage: python scripts/syllable_eval.py [library] [--lang fr] [--show 30]
Word-level exact agreement of the split (only multi-note words with the note text = the word).
"""
import argparse
import random
from collections import Counter, defaultdict
from pathlib import Path

from ultraai.lyrics import language_code, syllabify
from ultraai.ultrastar.reader import read


def hand_splits(song):
    for w in song.words():
        parts = [n.text.strip() for n in w.syllables]
        parts = [p for p in parts if p and p != "~"]
        if not parts or any("~" in p for p in parts):
            continue
        yield "".join(parts), parts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="library")
    ap.add_argument("--lang")
    ap.add_argument("--show", type=int, default=0)
    a = ap.parse_args()
    stats = defaultdict(Counter)
    bad = defaultdict(Counter)
    for d in sorted(Path(a.root).iterdir()):
        txt = next(iter(d.glob("*.txt")), None)
        if not txt:
            continue
        s = read(txt)
        lang = language_code(s.get("LANGUAGE"))
        if a.lang and lang != a.lang:
            continue
        for word, parts in hand_splits(s):
            ours = [p.strip() for p in syllabify(word, lang)]
            ok = ours == parts
            stats[lang]["ok" if ok else "bad"] += 1
            stats[lang]["count_ok"] += len(ours) == len(parts)
            if not ok:
                bad[lang][f"{'-'.join(parts):24} ours {'-'.join(ours)}"] += 1
    for lang, c in sorted(stats.items(), key=lambda x: -sum(x[1].values())):
        n = c["ok"] + c["bad"]
        print(f"{lang:6} words {n:6}  exact {100*c['ok']/n:5.1f}%  same count {100*c['count_ok']/n:5.1f}%")
        if a.show:
            for k, v in bad[lang].most_common(a.show):
                print(f"     {v:4}x  {k}")


if __name__ == "__main__":
    main()
