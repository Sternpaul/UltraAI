"""In-memory model of an UltraStar song.

Times inside the body are integer beats. One beat lasts ``60 / (4 * BPM)``
seconds and beat 0 sits at ``#GAP`` milliseconds into the audio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

NOTE_KINDS = (":", "*", "F", "R", "G")


def parse_number(value: str) -> float:
    """Parse a header number; UltraStar allows both ',' and '.' as decimal mark."""
    return float(value.strip().replace(",", "."))


def format_number(value: float, decimals: int = 2) -> str:
    """Format a number compactly ('300', '273.4')."""
    text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


@dataclass
class Note:
    kind: str          # one of NOTE_KINDS
    start: int         # beat
    length: int        # beats
    pitch: int         # semitones relative to C4 (MIDI = pitch + 60)
    text: str          # syllable incl. leading/trailing spaces; "~" = continuation

    @property
    def end(self) -> int:
        return self.start + self.length

    @property
    def is_golden(self) -> bool:
        return self.kind in ("*", "G")

    @property
    def is_rap(self) -> bool:
        return self.kind in ("R", "G")

    @property
    def is_freestyle(self) -> bool:
        return self.kind == "F"

    @property
    def is_pitched(self) -> bool:
        """True if the note's pitch is scored (normal or golden)."""
        return self.kind in (":", "*")

    @property
    def is_continuation(self) -> bool:
        """'~' notes continue the previous syllable on a new pitch (melisma)."""
        return self.text.strip().startswith("~")


@dataclass
class Line:
    """One screen line (phrase). ``break_beat`` is the '-' beat that ends it."""

    notes: list[Note] = field(default_factory=list)
    break_beat: int | None = None

    @property
    def start(self) -> int:
        return self.notes[0].start

    @property
    def end(self) -> int:
        return max(n.end for n in self.notes)

    @property
    def text(self) -> str:
        """Display text of the line, '~' continuations removed."""
        return "".join(display_text(n) for n in self.notes).strip()


def display_text(note: Note) -> str:
    """Text as shown on screen: a '~' continuation contributes only its whitespace."""
    if note.is_continuation:
        stripped = note.text.strip()
        rest = stripped[1:]
        lead = note.text[: len(note.text) - len(note.text.lstrip())]
        trail = note.text[len(note.text.rstrip()):]
        return lead + rest + trail
    return note.text


@dataclass
class Word:
    """A word assembled from consecutive notes (used by metrics/eval)."""

    text: str
    notes: list[Note]
    line_index: int

    @property
    def start(self) -> int:
        return self.notes[0].start

    @property
    def end(self) -> int:
        return max(n.end for n in self.notes)

    @property
    def syllables(self) -> list[Note]:
        """Notes that begin a new syllable (continuations excluded)."""
        return [n for n in self.notes if not n.is_continuation]


@dataclass
class Song:
    headers: list[tuple[str, str]] = field(default_factory=list)
    voices: dict[int, list[Line]] = field(default_factory=dict)  # 1 = P1, 2 = P2, ...
    # Information about the source file (not written back as headers).
    encoding: str = "utf-8"
    had_bom: bool = False
    was_relative: bool = False
    warnings: list[str] = field(default_factory=list)

    # ---- header access -------------------------------------------------
    def get(self, key: str, default: str | None = None) -> str | None:
        key = key.upper()
        for k, v in self.headers:
            if k == key:
                return v
        return default

    def set(self, key: str, value: str | None) -> None:
        """Set (or with ``None`` remove) a header, keeping its position."""
        key = key.upper()
        if value is None:
            self.headers = [(k, v) for k, v in self.headers if k != key]
            return
        for i, (k, _) in enumerate(self.headers):
            if k == key:
                self.headers[i] = (key, value)
                # drop duplicates
                self.headers = self.headers[: i + 1] + [
                    (k2, v2) for k2, v2 in self.headers[i + 1:] if k2 != key
                ]
                return
        self.headers.append((key, value))

    def has(self, key: str) -> bool:
        return self.get(key) not in (None, "")

    # ---- typed accessors ----------------------------------------------
    @property
    def title(self) -> str:
        return self.get("TITLE", "") or ""

    @property
    def artist(self) -> str:
        return self.get("ARTIST", "") or ""

    @property
    def bpm(self) -> float:
        value = self.get("BPM")
        if value is None:
            raise ValueError("song has no #BPM")
        return parse_number(value)

    @property
    def gap_ms(self) -> float:
        value = self.get("GAP")
        return parse_number(value) if value not in (None, "") else 0.0

    @property
    def version(self) -> tuple[int, int, int] | None:
        value = self.get("VERSION")
        if not value:
            return None
        parts = value.strip().split(".")
        if len(parts) != 3:
            raise ValueError(f"invalid #VERSION {value!r}")
        return tuple(int(p) for p in parts)  # type: ignore[return-value]

    @property
    def audio(self) -> str | None:
        """Audio file as USDX resolves it: #AUDIO wins over #MP3 for v1+."""
        if self.version and self.version >= (1, 0, 0) and self.has("AUDIO"):
            return self.get("AUDIO")
        return self.get("MP3") or None

    @property
    def is_duet(self) -> bool:
        return len([v for v in self.voices.values() if v]) > 1

    # ---- time conversion ----------------------------------------------
    @property
    def seconds_per_beat(self) -> float:
        return 60.0 / (4.0 * self.bpm)

    def beat_to_seconds(self, beat: float) -> float:
        return self.gap_ms / 1000.0 + beat * self.seconds_per_beat

    def seconds_to_beat(self, seconds: float) -> float:
        return (seconds - self.gap_ms / 1000.0) / self.seconds_per_beat

    # ---- iteration helpers --------------------------------------------
    def lines(self, voice: int = 1) -> list[Line]:
        return self.voices.get(voice, [])

    def notes(self, voice: int = 1) -> Iterator[Note]:
        for line in self.lines(voice):
            yield from line.notes

    def words(self, voice: int = 1) -> list[Word]:
        """Group notes into words using the whitespace in syllable texts."""
        words: list[Word] = []
        current: list[Note] = []
        current_line = 0

        def flush() -> None:
            if current:
                text = "".join(display_text(n) for n in current).strip()
                words.append(Word(text=text, notes=list(current), line_index=current_line))
                current.clear()

        for li, line in enumerate(self.lines(voice)):
            flush()  # a line break always ends a word
            current_line = li
            for note in line.notes:
                if note.text[:1].isspace() and not note.is_continuation:
                    flush()
                current.append(note)
                if note.text[-1:].isspace():
                    flush()
        flush()
        return words

    def lyrics(self, voice: int = 1) -> list[str]:
        """Plain lyric lines, one per screen line."""
        return [line.text for line in self.lines(voice)]
