// Live pitch checks for the editor: notes that do not fit what is sung.
//  - "off":     the note is >= 2 semitones away from the sung pitch (octave ignored)
//  - "moving":  a long flat note while the voice clearly moves (a melisma not split into ~ notes)
//  - "outlier": a huge jump away from both neighbours, or far outside the song's range, often
//               quiet too - backing vocals or separation noise turned into a note
// Marks generously on purpose: a false alarm costs a click ("OK"), a missed error costs a song.
import { isCont } from "./model.js";

const fold = d => { d = ((d % 12) + 12) % 12; return d > 6 ? d - 12 : d; };

function frames(editor, a, b, offset) {
  const out = [];
  const hop = editor.pitch_hop;
  for (let i = Math.max(0, Math.ceil(a / hop)); i <= Math.min(editor.pitch.length - 1, Math.floor(b / hop)); i++) {
    const v = editor.pitch[i];
    if (v !== null && v !== undefined) out.push(v - 60 + offset);
  }
  return out;
}

function median(xs) { const s = [...xs].sort((a, b) => a - b); return s.length ? s[s.length >> 1] : NaN; }

function smooth(xs, w = 7) {
  return xs.map((_, i) => median(xs.slice(Math.max(0, i - (w >> 1)), i + (w >> 1) + 1)));
}

function loudness(editor, a, b) {
  const pk = editor.peaks, hop = editor.peaks_hop;
  if (!pk?.length) return null;
  let s = 0, c = 0;
  for (let i = Math.floor(a / hop); i < Math.min(pk.length, Math.ceil(b / hop)); i++) { s += pk[i]; c++; }
  return c ? s / c : null;
}

export function pitchIssues(lines, editor, curveOffset = 0) {
  if (!editor?.pitch?.length) return [];
  const all = lines.flatMap(l => l.notes.filter(n => n.kind !== "F" && n.kind !== "R"));
  if (!all.length) return [];
  const ps = all.map(n => n.pitch).sort((a, b) => a - b);
  const lo = ps[Math.floor(0.02 * (ps.length - 1))], hi = ps[Math.floor(0.98 * (ps.length - 1))];
  const flagged = [];
  lines.forEach((ln, li) => {
    const notes = ln.notes.filter(n => n.kind !== "F" && n.kind !== "R");
    const lineLoud = median(notes.map(n => loudness(editor, n.start, n.end)).filter(x => x !== null));
    notes.forEach((n, k) => {
      const dur = n.end - n.start;
      const fr = frames(editor, n.start, n.end, curveOffset);
      // outlier: far from both neighbours / the song's range, often quiet
      const nb = [notes[k - 1], notes[k + 1]].filter(o => o && Math.min(Math.abs(o.start - n.end), Math.abs(n.start - o.end)) < 0.8);
      const jumpAll = nb.length && nb.every(o => Math.abs(o.pitch - n.pitch) >= 7);
      const outside = n.pitch > hi + 7 || n.pitch < lo - 7;
      const loud = loudness(editor, n.start, n.end);
      const quiet = lineLoud > 0 && loud !== null && loud < 0.45 * lineLoud;
      if ((jumpAll && (quiet || Math.abs(n.pitch - median(notes.map(x => x.pitch))) >= 7)) || outside) {
        flagged.push({ kind: "outlier", li, n, why: `${outside ? "far outside the song's range" : "a big jump away from both neighbours"}${quiet ? ", and much quieter" : ""} – backing vocals or noise?` });
        return;
      }
      if (fr.length < 4) return;
      const m = median(fr);
      const d = fold(n.pitch - m);
      if (Math.abs(d) >= 2) {
        flagged.push({ kind: "off", li, n, d, why: `note is ${Math.abs(Math.round(d))} semitones ${d > 0 ? "above" : "below"} what is sung` });
        return;
      }
      if (dur >= 0.35 && !isCont(ln.notes[ln.notes.indexOf(n) + 1] ?? { text: "" })) {
        const sm = smooth(fr.map(v => m + fold(v - m)));
        const sorted = [...sm].sort((a, b) => a - b);
        const range = sorted[Math.floor(0.9 * (sorted.length - 1))] - sorted[Math.floor(0.1 * (sorted.length - 1))];
        if (range >= 2.5) flagged.push({ kind: "moving", li, n, why: `the voice moves ${Math.round(range)} semitones here but the note is flat – split it (S) or add ~ notes` });
      }
    });
  });
  // one issue per run of neighbouring notes of the same kind in a line
  const issues = [];
  for (const f of flagged) {
    const last = issues[issues.length - 1];
    if (last && last.pk === f.kind && last.li === f.li && f.n.start - last.end < 0.6) {
      last.end = f.n.end; last.notes.push(f.n); continue;
    }
    issues.push({ pk: f.kind, li: f.li, start: f.n.start, end: f.n.end, notes: [f.n], why: f.why, d: f.d });
  }
  const risk = { outlier: 0.85, off: 0.6, moving: 0.55 };
  const label = { outlier: "Unusual note", off: "Note does not match the voice", moving: "Flat note over a moving voice" };
  return issues.map(i => ({
    kind: `pitch_${i.pk}`, start: i.start, end: i.end, notes: i.notes,
    risk: i.pk === "off" ? Math.min(0.85, risk.off + 0.05 * (Math.abs(i.d) - 2)) : risk[i.pk],
    text: `${label[i.pk]} (${i.notes.map(n => n.text.trim() || "~").join(" ")})`,
    reasons: [i.why], key: `p:${i.pk}:${Math.round(i.start * 2)}`,
  }));
}
