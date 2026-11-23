// Compare the song's words with the lyrics it was made from: missing and extra words.
// The song may repeat lyric lines (a chorus written once, sung three times): going back to the
// start of any lyric line is allowed.  Skipping ahead costs a "missing" word per word skipped.

const MATCH = 1, EXTRA = -1, MISSING = -1, JUMP = -2;

export function norm(w) {
  return w.toLowerCase().normalize("NFKD").replace(/\p{M}/gu, "").replace(/[^\p{L}\p{N}]/gu, "");
}

/** Words of the song: {text, key, notes, start, end}. A word ends where a note's text ends with a space. */
export function songWords(lines) {
  const out = [];
  for (const ln of lines) {
    let cur = null;
    for (const n of ln.notes) {
      const t = n.text;
      const cont = t.trim().startsWith("~");
      if (!cur || (!cont && /^\s/.test(t))) {
        if (cur) out.push(cur);
        cur = { text: "", notes: [] };
      }
      cur.text += cont ? t.trim().slice(1) : t.trim();
      cur.notes.push(n);
      if (/\s$/.test(t)) { out.push(cur); cur = null; }
    }
    if (cur) out.push(cur);
  }
  return out.map(w => ({ ...w, key: norm(w.text), start: w.notes[0].start, end: w.notes[w.notes.length - 1].end }))
    .filter(w => w.key);
}

/** {extra: [song word], missing: [{words: [lyric words], before: song word index, t}]} */
export function diffLyrics(lines, lyricLines) {
  const sw = songWords(lines);
  const lw = [], lineStart = new Set();
  for (const text of lyricLines) {
    lineStart.add(lw.length);
    for (const w of text.split(/\s+/)) { const k = norm(w); if (k) lw.push({ text: w, key: k }); }
  }
  const M = lw.length, N = sw.length;
  if (!M || !N) return { extra: [], missing: [], words: sw };
  const starts = [...lineStart].filter(s => s < M).sort((a, b) => a - b);
  const NEG = -1e15;
  // V[p]: best score with p = index of the next lyric word.  Per row: kind (0 match, 1 missing,
  // 2 jump, 3 extra, -1 none) and the previous p.
  let V = new Float64Array(M + 1).fill(NEG);
  V[0] = 0;
  const kinds = [], prevs = [];
  const eps = (V, kd, pv) => {
    for (let p = 1; p <= M; p++) {           // skip lyric words (missing)
      const v = V[p - 1] + MISSING;
      if (v > V[p]) { V[p] = v; kd[p] = 1; pv[p] = p - 1; }
    }
    let bestV = NEG, bestB = -1;             // jump back to a line start (repeat)
    for (let i = M; i >= 0; i--) {
      if (lineStart.has(i) && bestB > i && bestV + JUMP > V[i]) { V[i] = bestV + JUMP; kd[i] = 2; pv[i] = bestB; }
      if (V[i] > bestV) { bestV = V[i]; bestB = i; }
    }
  };
  const row = () => [new Int8Array(M + 1).fill(-1), new Int32Array(M + 1)];
  let [kd, pv] = row();
  eps(V, kd, pv); kinds.push(kd); prevs.push(pv);
  for (let n = 0; n < N; n++) {
    const W = new Float64Array(M + 1).fill(NEG);
    [kd, pv] = row();
    const k = sw[n].key;
    for (let p = 0; p <= M; p++) {
      if (V[p] <= NEG / 2) continue;
      const vi = V[p] + EXTRA;                 // song word not in the lyrics
      if (vi > W[p]) { W[p] = vi; kd[p] = 3; pv[p] = p; }
      if (p < M && lw[p].key === k) {
        const vm = V[p] + MATCH;
        if (vm > W[p + 1]) { W[p + 1] = vm; kd[p + 1] = 0; pv[p + 1] = p; }
      }
    }
    eps(W, kd, pv); kinds.push(kd); prevs.push(pv); V = W;
  }
  // end: lyric words after the end position are missing too
  let end = 0, best = NEG;
  for (let p = 0; p <= M; p++) { const v = V[p] + MISSING * (M - p); if (v > best) { best = v; end = p; } }
  const extra = [], miss = [];
  for (let q = end; q < M; q++) miss.push([q, N]);
  let n = N, p = end;
  for (let guard = 0; guard < 4 * (N + M) + 10; guard++) {
    const kind = kinds[n][p];
    if (kind < 0) break;
    const q = prevs[n][p];
    if (kind === 0) { n -= 1; p = q; }                        // match
    else if (kind === 3) { extra.push(n - 1); n -= 1; }       // extra song word
    else if (kind === 1) { miss.push([q, n]); p = q; }         // lyric word q missing before song word n
    else { p = q; }                                            // jump back (repeat)
  }
  // group consecutive missing words at the same place
  miss.sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  const missing = [];
  for (const [q, before] of miss) {
    const last = missing[missing.length - 1];
    if (last && last.before === before && last.qs[last.qs.length - 1] === q - 1) { last.qs.push(q); continue; }
    missing.push({ before, qs: [q] });
  }
  return {
    words: sw,
    extra: extra.reverse().map(i => sw[i]),
    missing: missing.map(m => ({
      text: m.qs.map(q => lw[q].text).join(" "), q: m.qs[0], before: m.before,
      t: m.before < N ? sw[m.before].start : (sw[N - 1]?.end ?? 0),
      after: m.before > 0 ? sw[m.before - 1].text : "", next: m.before < N ? sw[m.before].text : "",
    })),
  };
}

/** Issues for the editor's list. */
export function lyricIssues(lines, lyricLines) {
  if (!lyricLines?.length) return [];
  const d = diffLyrics(lines, lyricLines);
  const out = [];
  // consecutive extra words -> one issue
  let run = null;
  for (const w of d.extra) {
    if (run && d.words.indexOf(w) === run.last + 1) { run.ws.push(w); run.last++; continue; }
    run = { ws: [w], last: d.words.indexOf(w) };
    out.push(run);
  }
  const issues = out.map(r => ({
    kind: "lyrics_extra", start: r.ws[0].start, end: r.ws[r.ws.length - 1].end, risk: 0.7,
    text: r.ws.map(w => w.text).join(" "), notes: r.ws.flatMap(w => w.notes),
    reasons: ["sung in the song but not in the lyrics"], key: `lx:${r.ws.map(w => w.key).join("_")}:${r.ws[0].start.toFixed(0)}`,
  }));
  for (const m of d.missing) {
    issues.push({
      kind: "lyrics_missing", start: m.t, end: m.t + 0.3, risk: 0.75, text: m.text,
      reasons: [m.after || m.next ? `missing ${m.after ? `after “${m.after}”` : `before “${m.next}”`}` : "missing in the song"],
      key: `lm:${m.q}:${m.text.slice(0, 20)}`,
    });
  }
  return issues;
}
