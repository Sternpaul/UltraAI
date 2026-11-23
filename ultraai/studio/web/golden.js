// Golden note presets: which notes score double.  Works on syllables (a note plus its "~"
// continuation notes), keeps freestyle/rap notes as they are, and marks the same syllables in
// every repeat of a line.
import { isCont, lineText } from "./model.js";

export const PRESETS = [
  ["hard", "Hard jumps", "notes reached by a big pitch jump – the hard-to-hit spots"],
  ["held", "Big held notes", "long, strong notes in repeated lines (the chorus “whoaaa”s)"],
  ["high", "Highest notes", "the top of the song's range (e.g. the octave-up last chorus)"],
  ["mix", "Mix", "a bit of everything above"],
  ["none", "None", "no golden notes"],
];

function groups(lines) {
  const out = [];
  lines.forEach((ln, li) => {
    let g = null;
    ln.notes.forEach((n, ni) => {
      if (g && isCont(n)) { g.notes.push(n); return; }
      g = { li, ni, notes: [n] };
      out.push(g);
    });
  });
  for (const g of out) {
    g.start = g.notes[0].start;
    g.end = g.notes[g.notes.length - 1].end;
    g.dur = g.end - g.start;
    g.pitch = Math.max(...g.notes.map(n => n.pitch));
    g.free = g.notes.every(n => n.kind === "F" || n.kind === "R" || n.kind === "G");
  }
  return out;
}

const fold = (p, ref) => { while (p - ref > 6) p -= 12; while (ref - p > 6) p += 12; return p; };

function energy(editor, a, b) {
  const pk = editor?.peaks, hop = editor?.peaks_hop;
  if (!pk?.length) return 0.5;
  let s = 0, c = 0;
  for (let i = Math.floor(a / hop); i < Math.min(pk.length, Math.ceil(b / hop)); i++) { s += pk[i]; c++; }
  return c ? s / c / 255 : 0;
}

function lineKey(ln) { return lineText(ln).toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, "").replace(/\s+/g, " ").trim(); }

/** Re-mark golden notes for the whole song. share: fraction of notes (default 4 %). */
export function applyPreset(lines, preset, editor, share = 0.04) {
  const gs = groups(lines);
  for (const g of gs) for (const n of g.notes) if (n.kind === "*") n.kind = ":";
  if (preset === "none" || !gs.length) return 0;
  const counts = new Map();
  for (const ln of lines) { const k = lineKey(ln); counts.set(k, (counts.get(k) || 0) + 1); }
  const pitches = gs.map(g => g.pitch).sort((a, b) => a - b);
  const p90 = pitches[Math.floor(0.9 * (pitches.length - 1))];
  const byLine = new Map();
  for (const g of gs) { if (!byLine.has(g.li)) byLine.set(g.li, []); byLine.get(g.li).push(g); }
  const score = { hard: new Map(), held: new Map(), high: new Map() };
  for (const [li, row] of byLine) {
    const med = row.map(g => g.pitch).sort((a, b) => a - b)[row.length >> 1];
    const repeated = (counts.get(lineKey(lines[li])) || 0) > 1;
    row.forEach((g, k) => {
      if (g.free) return;
      let jump = 0;
      for (const j of [k - 1, k + 1]) {
        const o = row[j];
        if (!o || (j > k ? o.start - g.end : g.start - o.end) > 0.6) continue;
        let d = Math.abs(g.pitch - o.pitch);
        if (d >= 11 && d <= 13) d = Math.abs(d - 12);
        jump = Math.max(jump, d);
      }
      if (jump >= 4 && g.dur >= 0.3) score.hard.set(g, jump + 0.5 * Math.abs(fold(g.pitch, med) - med));
      if (repeated && g.dur >= 0.5) score.held.set(g, g.dur * (0.5 + energy(editor, g.start, g.end)));
      if (g.pitch >= p90 && g.dur >= 0.25) score.high.set(g, (g.pitch - p90 + 1) * Math.min(g.dur, 1.5));
    });
  }
  let final;
  if (preset === "mix") {
    final = new Map();
    for (const m of Object.values(score)) {
      const mx = Math.max(1e-9, ...m.values());
      for (const [g, v] of m) final.set(g, (final.get(g) || 0) + v / mx);
    }
  } else final = score[preset] || new Map();
  const total = gs.reduce((s, g) => s + g.notes.length, 0);
  const budget = Math.max(1, Math.round(share * total));
  const chosen = new Set();
  let used = 0;
  for (const [g] of [...final].sort((a, b) => b[1] - a[1])) {
    if (used >= budget) break;
    chosen.add(g); used += g.notes.length;
  }
  // the same syllables in every repeat of a line
  const byKey = new Map();
  lines.forEach((ln, li) => { const k = `${lineKey(ln)}|${ln.notes.length}`; if (!byKey.has(k)) byKey.set(k, []); byKey.get(k).push(li); });
  for (const g of [...chosen]) {
    const ln = lines[g.li];
    for (const other of byKey.get(`${lineKey(ln)}|${ln.notes.length}`) || []) {
      const h = gs.find(x => x.li === other && x.ni === g.ni);
      if (h && !h.free) chosen.add(h);
    }
  }
  for (const g of chosen) for (const n of g.notes) if (n.kind === ":") n.kind = "*";
  return chosen.size;
}
