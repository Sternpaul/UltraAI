// Repeated lines and sections (choruses), exact or nearly the same, and linked editing:
// a change to one occurrence is applied relatively to the others.
import { lineEnd, lineStart, lineText } from "./model.js";

const key = ln => lineText(ln).toLowerCase().normalize("NFKD").replace(/\p{M}/gu, "")
  .replace(/[^\p{L}\p{N}\s]/gu, "").replace(/\s+/g, " ").trim();

function similar(a, b) {  // word overlap of two normalized lines (0..1)
  if (a === b) return 1;
  const wa = a.split(" "), wb = b.split(" ");
  if (!wa.length || !wb.length) return 0;
  const sb = new Map();
  for (const w of wb) sb.set(w, (sb.get(w) || 0) + 1);
  let common = 0;
  for (const w of wa) if (sb.get(w) > 0) { common++; sb.set(w, sb.get(w) - 1); }
  return (2 * common) / (wa.length + wb.length);
}

/**
 * groups:   [{lines: [line idx], exact: bool, text}] - lines sung the same (>= 2 members)
 * byLine:   Map line idx -> group
 * sections: [{len, starts: [first line idx], text}] - runs of >= 2 lines that repeat
 */
export function findRepeats(lines, near = 0.8) {
  const keys = lines.map(key);
  const parent = lines.map((_, i) => i);
  const find = i => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  const exactPair = new Set();
  for (let i = 0; i < lines.length; i++) {
    if (!keys[i]) continue;
    for (let j = i + 1; j < lines.length; j++) {
      if (!keys[j]) continue;
      const s = similar(keys[i], keys[j]);
      if (s >= near && Math.min(keys[i].length, keys[j].length) >= 2) {
        parent[find(j)] = find(i);
        if (s === 1) exactPair.add(`${i}:${j}`);
      }
    }
  }
  const members = new Map();
  lines.forEach((_, i) => { if (keys[i]) { const r = find(i); if (!members.has(r)) members.set(r, []); members.get(r).push(i); } });
  const groups = [];
  const byLine = new Map();
  for (const ls of members.values()) {
    if (ls.length < 2) continue;
    const g = { lines: ls, exact: ls.every(i => keys[i] === keys[ls[0]]), text: lineText(lines[ls[0]]) };
    groups.push(g);
    for (const i of ls) byLine.set(i, g);
  }
  // sections: maximal runs i..i+k that repeat at j..j+k
  const gid = i => byLine.get(i) ?? null;
  const runs = new Map();
  for (let i = 0; i < lines.length; i++) {
    for (let j = i + 1; j < lines.length; j++) {
      if (!gid(i) || gid(i) !== gid(j)) continue;
      if (i > 0 && gid(i - 1) && gid(i - 1) === gid(j - 1)) continue;  // not the start of the run
      let k = 0;
      while (i + k < j && j + k < lines.length && gid(i + k) && gid(i + k) === gid(j + k)) k++;
      if (k < 2) continue;
      const sig = Array.from({ length: k }, (_, t) => groups.indexOf(gid(i + t))).join(",");
      if (!runs.has(sig)) runs.set(sig, { len: k, starts: new Set(), text: lineText(lines[i]) });
      runs.get(sig).starts.add(i); runs.get(sig).starts.add(j);
    }
  }
  const sections = [...runs.values()].map(r => ({ ...r, starts: [...r.starts].sort((a, b) => a - b) }))
    .filter(r => r.starts.every((s, n, a) => n === 0 || s >= a[n - 1] + r.len))  // no overlapping copies
    .sort((a, b) => b.len - a.len || b.starts.length - a.starts.length);
  // drop sections contained in a longer one with the same starts
  const kept = [];
  for (const s of sections) {
    if (kept.some(k => k.len > s.len && s.starts.every(x => k.starts.some(y => x >= y && x + s.len <= y + k.len)))) continue;
    kept.push(s);
  }
  return { groups, byLine, sections: kept };
}

const clone = ln => JSON.parse(JSON.stringify(ln));

/**
 * After an edit: apply what changed in each edited line to the other lines of its group.
 * Same number of notes -> the same changes per note (time shift, length, pitch step, kind,
 * syllable).  Different number (split/merge/new note) in an exactly repeated line -> the
 * edited line's notes are copied, placed at the other occurrence's own start.
 * Returns {applied, skipped}.
 */
export function propagate(prevLines, lines, rep) {
  let applied = 0, skipped = 0;
  if (prevLines.length !== lines.length) return { applied, skipped };
  const changed = [];
  for (let i = 0; i < lines.length; i++) if (JSON.stringify(prevLines[i]) !== JSON.stringify(lines[i])) changed.push(i);
  const touched = new Set(changed);
  for (const i of changed) {
    const g = rep.byLine.get(i);
    if (!g) continue;
    const before = prevLines[i], after = lines[i];
    for (const o of g.lines) {
      if (touched.has(o)) continue;
      const other = lines[o];
      const same = before.notes.length === after.notes.length && other.notes.length === before.notes.length;
      if (same) {
        after.notes.forEach((n, k) => {
          const b = before.notes[k], t = other.notes[k];
          t.start += n.start - b.start;
          t.end += n.end - b.end;
          t.pitch += n.pitch - b.pitch;
          if (n.kind !== b.kind) t.kind = n.kind;
          if (n.text !== b.text && g.exact) t.text = n.text;
        });
        applied++;
      } else if (g.exact && other.notes.length) {
        const shift = lineStart(other) - lineStart(before);
        other.notes = clone(after).notes.map(n => ({ ...n, start: n.start + shift, end: n.end + shift }));
        applied++;
      } else {
        skipped++;
      }
      touched.add(o);
    }
  }
  return { applied, skipped };
}

export function spanOf(lines, i, len = 1) {
  return [lineStart(lines[i]), lineEnd(lines[i + len - 1] ?? lines[i])];
}
