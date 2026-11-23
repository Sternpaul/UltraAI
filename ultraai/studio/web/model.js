// Song document operations (times in seconds; pitch = txt value, semitones relative to C4).

export const MIN_NOTE = 0.04;

export function flatten(lines) {
  const out = [];
  lines.forEach((ln, li) => ln.notes.forEach((n, ni) => out.push({ n, li, ni })));
  return out;
}

export function isCont(n) { return n.text.trim().startsWith("~"); }

/** Display text of a note (a "~" continuation shows nothing but its spacing). */
export function shown(n) {
  if (!isCont(n)) return n.text;
  const lead = n.text.match(/^\s*/)[0], trail = n.text.match(/\s*$/)[0];
  return lead + n.text.trim().slice(1) + trail;
}

export function lineText(line) { return line.notes.map(shown).join("").replace(/\s+/g, " ").trim(); }

export function lineStart(line) { return line.notes.length ? line.notes[0].start : 0; }
export function lineEnd(line) { return line.notes.reduce((m, n) => Math.max(m, n.end), 0); }

/** "Re|lease my bo|dy" for the line editor ("~" notes are melisma, not syllables). */
export function lineToEditText(line) {
  let out = "";
  line.notes.forEach((n, k) => {
    if (isCont(n)) { out += n.text.trim().slice(1) ? n.text.trim().slice(1) : ""; if (/\s$/.test(n.text)) out += " "; return; }
    const t = n.text;
    const startsWord = k === 0 || /^\s/.test(t) || /\s$/.test(line.notes[k - 1].text);
    if (!startsWord && out && !/\s$/.test(out)) out += "|";
    out += t.trim();
    if (/\s$/.test(t)) out += " ";
  });
  return out.replace(/\s+/g, " ").trim();
}

/** Parse "Re|lease my bo|dy" -> ["Re", "lease ", "my ", "bo", "dy "] (trailing space ends a word). */
export function parseEditText(text) {
  const out = [];
  for (const word of text.trim().split(/\s+/).filter(Boolean)) {
    const parts = word.split("|").filter(Boolean);
    parts.forEach((p, k) => out.push(p + (k === parts.length - 1 ? " " : "")));
  }
  return out;
}

/**
 * Apply edited text to a line.  Same number of syllables: only the texts change.
 * Otherwise the syllables are re-timed over the sung parts of the old notes (weighted by
 * length) and their pitch taken from the singer's curve where possible.
 */
export function applyLineText(line, text, pitchAt) {
  const syl = parseEditText(text);
  if (!syl.length) return false;
  const real = line.notes.filter(n => !isCont(n));
  if (syl.length === real.length) {
    real.forEach((n, k) => { n.text = syl[k]; });
    return true;
  }
  const spans = line.notes.map(n => [n.start, n.end]);
  const total = spans.reduce((s, [a, b]) => s + (b - a), 0);
  const weights = syl.map(s => Math.max(1, s.trim().replace(/[^\p{L}\p{N}]/gu, "").length));
  const wsum = weights.reduce((a, b) => a + b, 0);
  const at = x => { // sung-time offset -> absolute time
    for (const [a, b] of spans) { if (x <= b - a) return a + x; x -= b - a; }
    return spans[spans.length - 1][1];
  };
  const notes = [];
  let acc = 0;
  syl.forEach((s, k) => {
    const a = at(acc), b = at(acc + total * weights[k] / wsum);
    acc += total * weights[k] / wsum;
    const old = line.notes.find(n => n.start <= (a + b) / 2 && n.end >= (a + b) / 2) || line.notes[0];
    const p = pitchAt ? pitchAt(a, Math.max(a + MIN_NOTE, b), old.pitch) : old.pitch;
    notes.push({ start: a, end: Math.max(a + MIN_NOTE, b - 0.01), pitch: p, text: s, kind: old.kind === "*" ? ":" : old.kind });
  });
  line.notes = notes;
  return true;
}

/** Keep notes sorted, non-overlapping where they were, breaks between lines. */
export function normalize(lines) {
  for (const ln of lines) ln.notes.sort((a, b) => a.start - b.start);
  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i], nxt = lines[i + 1];
    if (!ln.notes.length) continue;
    if (!nxt || !nxt.notes.length) { if (ln.break !== null && ln.break !== undefined) ln.break = Math.max(ln.break, lineEnd(ln)); continue; }
    const end = lineEnd(ln), ns = lineStart(nxt);
    if (ln.break === null || ln.break === undefined || ln.break < end - 1e-6 || ln.break > ns + 1e-6) {
      ln.break = ns >= end ? end + Math.min(0.5, (ns - end) / 2) : end;
    }
  }
  for (let i = lines.length - 1; i >= 0; i--) if (!lines[i].notes.length) lines.splice(i, 1);
}

/** Median sung pitch (txt units) in [a, b], moved to the octave nearest ``near``. */
export function curvePitch(editor, a, b, near) {
  if (!editor?.pitch?.length) return near;
  const hop = editor.pitch_hop;
  const vals = [];
  for (let i = Math.max(0, Math.floor(a / hop)); i <= Math.min(editor.pitch.length - 1, Math.ceil(b / hop)); i++) {
    const v = editor.pitch[i];
    if (v !== null && v !== undefined) vals.push(v);
  }
  if (vals.length < 2) return near;
  vals.sort((x, y) => x - y);
  let p = Math.round(vals[vals.length >> 1]) - 60;
  while (p - near > 6) p -= 12;
  while (near - p > 6) p += 12;
  return p;
}

/** Octave shift (multiple of 12) between the singer's curve and the notes, for drawing. */
export function curveOffset(editor, lines) {
  if (!editor?.pitch?.length) return 0;
  const d = [];
  for (const ln of lines) for (const n of ln.notes) {
    const i = Math.round((n.start + n.end) / 2 / editor.pitch_hop);
    const v = editor.pitch[i];
    if (v !== null && v !== undefined) d.push(v - 60 - n.pitch);
  }
  if (!d.length) return 0;
  d.sort((x, y) => x - y);
  return -12 * Math.round(d[d.length >> 1] / 12);
}
