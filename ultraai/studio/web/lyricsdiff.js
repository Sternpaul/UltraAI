// Compare the song's words with the lyrics it was made from: missing and extra words.
// Words are compared piece by piece ("20-odd" = "20" + "odd", "been 20" on one note = "been" +
// "20"), so how syllables are spread over notes does not matter.  The song may repeat lyric
// lines (a chorus written once, sung three times): going back to the start of any lyric line is
// allowed; skipping ahead costs one "missing" per lyric word skipped.

const MATCH = 1, EXTRA = -1, MISSING = -1, JUMP = -2;
const SPLIT = /[\s\-‐‑–—/]+/;

export function norm(w) {
  return w.toLowerCase().normalize("NFKD").replace(/\p{M}/gu, "").replace(/[^\p{L}\p{N}]/gu, "");
}

/** Song tokens: {text, key, notes, start, end, li}; a word ends where a note's text ends with a space. */
export function songWords(lines) {
  const words = [];
  lines.forEach((ln, li) => {
    let cur = null;
    for (const n of ln.notes) {
      const t = n.text;
      const cont = t.trim().startsWith("~");
      if (!cur || (!cont && /^\s/.test(t))) {
        if (cur) words.push(cur);
        cur = { notes: [], li };
      }
      cur.notes.push(n);
      if (/\s$/.test(t)) { words.push(cur); cur = null; }
    }
    if (cur) words.push(cur);
  });
  const out = [];
  for (const w of words) {
    // "been 20" on one note is two words; "20-odd" is two words
    const joined = w.notes.map(n => n.text.trim().replace(/^~/, "")).join("");
    for (const piece of joined.split(SPLIT)) {
      const key = norm(piece);
      if (key) out.push({ text: piece, key, notes: w.notes, li: w.li, start: w.notes[0].start, end: w.notes[w.notes.length - 1].end });
    }
  }
  return out;
}

function lyricWords(lyricLines) {
  const lw = [], lineStart = new Set();
  lyricLines.forEach((text, L) => {
    lineStart.add(lw.length);
    for (const piece of text.split(SPLIT)) { const k = norm(piece); if (k) lw.push({ text: piece, key: k, L }); }
  });
  return { lw, lineStart };
}

/** Alignment: {words, lw, match: song token -> lyric index | -1, extra: [token idx], missing: [{qs, before}]} */
export function diffLyrics(lines, lyricLines) {
  const sw = songWords(lines);
  const { lw, lineStart } = lyricWords(lyricLines);
  const M = lw.length, N = sw.length;
  if (!M || !N) return { words: sw, lw, match: sw.map(() => -1), extra: [], missing: [] };
  const NEG = -1e15;
  let V = new Float64Array(M + 1).fill(NEG);
  V[0] = 0;
  const kinds = [], prevs = [];   // per row: 0 match, 1 missing, 2 jump, 3 extra, -1 none
  const eps = (V, kd, pv) => {
    for (let p = 1; p <= M; p++) {
      const v = V[p - 1] + MISSING;
      if (v > V[p]) { V[p] = v; kd[p] = 1; pv[p] = p - 1; }
    }
    let bestV = NEG, bestB = -1;
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
      const vi = V[p] + EXTRA;
      if (vi > W[p]) { W[p] = vi; kd[p] = 3; pv[p] = p; }
      if (p < M && lw[p].key === k) {
        const vm = V[p] + MATCH;
        if (vm > W[p + 1]) { W[p + 1] = vm; kd[p + 1] = 0; pv[p + 1] = p; }
      }
    }
    eps(W, kd, pv); kinds.push(kd); prevs.push(pv); V = W;
  }
  let end = 0, best = NEG;
  for (let p = 0; p <= M; p++) { const v = V[p] + MISSING * (M - p); if (v > best) { best = v; end = p; } }
  const match = sw.map(() => -1), extra = [], miss = [];
  for (let q = end; q < M; q++) miss.push([q, N]);
  let n = N, p = end;
  for (let guard = 0; guard < 4 * (N + M) + 10; guard++) {
    const kind = kinds[n][p];
    if (kind < 0) break;
    const q = prevs[n][p];
    if (kind === 0) { match[n - 1] = q; n -= 1; p = q; }
    else if (kind === 3) { extra.push(n - 1); n -= 1; }
    else if (kind === 1) { miss.push([q, n]); p = q; }
    else { p = q; }
  }
  miss.sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  const missing = [];
  for (const [q, before] of miss) {
    const last = missing[missing.length - 1];
    if (last && last.before === before && last.qs[last.qs.length - 1] === q - 1) { last.qs.push(q); continue; }
    missing.push({ before, qs: [q] });
  }
  return { words: sw, lw, match, extra: extra.reverse(), missing };
}

/** The lyric line and the song line side by side: [{text, mark: "" | "missing" | "extra"}]. */
function compare(d, L, li) {
  const lyric = d.lw.map((w, q) => ({ w, q })).filter(x => x.w.L === L);
  const matched = new Set(d.match.filter(q => q >= 0));
  const lyricRow = lyric.map(({ w, q }) => ({ text: w.text, mark: matched.has(q) ? "" : "missing" }));
  const extraSet = new Set(d.extra);
  const songRow = d.words.map((w, i) => ({ w, i })).filter(x => x.w.li === li)
    .map(({ w, i }) => ({ text: w.text, mark: extraSet.has(i) ? "extra" : "" }));
  return { lyric: lyricRow, song: songRow };
}

/** Issues for the editor's list, each with ``compare`` rows for display. */
export function lyricIssues(lines, lyricLines) {
  if (!lyricLines?.length) return [];
  const d = diffLyrics(lines, lyricLines);
  const N = d.words.length;
  const lyricLineNear = i => {  // lyric line of the nearest matched song token
    for (let r = 0; r < N; r++) {
      for (const j of [i - r, i + r]) if (j >= 0 && j < N && d.match[j] >= 0) return d.lw[d.match[j]].L;
    }
    return 0;
  };
  const issues = [];
  let run = null;
  for (const i of d.extra) {
    if (run && i === run.last + 1 && d.words[i].li === d.words[run.last].li) { run.idx.push(i); run.last = i; continue; }
    run = { idx: [i], last: i };
    issues.push(run);
  }
  const out = issues.map(r => {
    const ws = r.idx.map(i => d.words[i]);
    const li = ws[0].li;
    return {
      kind: "lyrics_extra", start: ws[0].start, end: ws[ws.length - 1].end, risk: 0.7,
      text: ws.map(w => w.text).join(" "), notes: [...new Set(ws.flatMap(w => w.notes))],
      reasons: ["in the song but not in the lyrics"], compare: compare(d, lyricLineNear(r.idx[0]), li),
      key: `lx:${ws.map(w => w.key).join("_")}:${ws[0].start.toFixed(0)}`,
    };
  });
  for (const m of d.missing) {
    const before = m.before;
    const t = before < N ? d.words[before].start : (d.words[N - 1]?.end ?? 0);
    const li = before < N ? d.words[before].li : d.words[N - 1]?.li ?? 0;
    const lineFirst = before < N && (before === 0 || d.words[before - 1].li !== d.words[before].li);
    const after = before > 0 && !lineFirst ? d.words[before - 1].text : "", next = before < N ? d.words[before].text : "";
    const L = d.lw[m.qs[0]].L;
    out.push({
      kind: "lyrics_missing", start: t, end: t + 0.3, risk: 0.75, text: m.qs.map(q => d.lw[q].text).join(" "),
      reasons: [lineFirst ? `missing at the start of the line (before “${next}”)`
        : after || next ? `missing ${after ? `after “${after}”` : `before “${next}”`}` : "missing in the song"],
      compare: compare(d, L, li), key: `lm:${m.qs[0]}:${m.qs.length}`,
    });
  }
  return out;
}
