// Game-style preview: note bars of the current line, lyrics with karaoke fill, next line below.
import { isCont, lineEnd, lineStart, shown } from "./model.js";

export function drawPreview(canvas, lines, now) {
  const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(r.width * dpr) || canvas.height !== Math.round(r.height * dpr)) {
    canvas.width = Math.round(r.width * dpr); canvas.height = Math.round(r.height * dpr);
  }
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  const W = r.width, H = r.height;
  const bg = g.createLinearGradient(0, 0, 0, H);
  bg.addColorStop(0, "#141a3a"); bg.addColorStop(1, "#0a0c18");
  g.fillStyle = bg; g.fillRect(0, 0, W, H);

  // current line: the one being sung, else the next one to come (USDX shows it early)
  let li = lines.findIndex((ln, i) => ln.notes.length && now < (lines[i].break ?? lineEnd(ln) + 0.5));
  if (li < 0) li = lines.length - 1;
  const cur = lines[li], next = lines[li + 1];
  if (!cur || !cur.notes.length) return;

  // note bars
  const a = lineStart(cur), b = lineEnd(cur);
  const pitches = cur.notes.map(n => n.pitch);
  const pmin = Math.min(...pitches), pmax = Math.max(...pitches);
  const top = 14, bottom = H * 0.56, left = W * 0.06, right = W * 0.94;
  const span = Math.max(pmax - pmin, 8);
  const bh = Math.max(7, Math.min(14, (bottom - top) / (span + 2)));
  const xOf = t => left + (t - a) / Math.max(0.5, b - a) * (right - left);
  const yOf = p => bottom - bh - (p - (pmin + pmax - span) / 2) / span * (bottom - top - bh);
  for (const n of cur.notes) {
    const x0 = xOf(n.start), x1 = xOf(n.end), y = yOf(n.pitch);
    const golden = n.kind === "*", free = n.kind === "F";
    g.globalAlpha = free ? 0.35 : 1;
    g.fillStyle = "rgba(255,255,255,.18)";
    pill(g, x0, y, Math.max(bh, x1 - x0), bh); g.fill();
    if (now > n.start) { // sung part
      const xs = Math.min(x1, xOf(now));
      g.fillStyle = golden ? "#ffd24a" : "#6fb4ff";
      pill(g, x0, y, Math.max(bh, xs - x0), bh); g.fill();
    }
    g.strokeStyle = golden ? "#ffd24a" : "rgba(255,255,255,.6)";
    pill(g, x0, y, Math.max(bh, x1 - x0), bh); g.stroke();
    g.globalAlpha = 1;
  }
  if (now >= a && now <= b) { g.fillStyle = "#ff5a5a"; g.fillRect(xOf(now), top - 6, 2, bottom - top + 10); }

  // lyrics with karaoke fill per syllable
  lyric(g, cur, now, W / 2, H * 0.74, Math.min(28, W / 30), true);
  if (next) lyric(g, next, -1, W / 2, H * 0.92, Math.min(19, W / 44), false);
}

function lyric(g, line, now, cx, y, size, active) {
  g.font = `600 ${size}px system-ui, "Segoe UI", sans-serif`;
  g.textBaseline = "middle";
  const parts = line.notes.map(n => ({ n, text: isCont(n) ? shown(n) : n.text }));
  const full = parts.map(p => p.text).join("").replace(/\s+/g, " ").trim();
  let x = cx - g.measureText(full).width / 2;
  let started = false;
  for (const { n, text } of parts) {
    let t = text;
    if (!started) { t = t.replace(/^\s+/, ""); started = !!t; }
    if (!t) continue;
    const w = g.measureText(t).width;
    g.fillStyle = active ? "rgba(255,255,255,.92)" : "rgba(255,255,255,.45)";
    g.fillText(t, x, y);
    if (active && now > n.start) {
      const frac = Math.min(1, (now - n.start) / Math.max(0.05, n.end - n.start));
      g.save();
      g.beginPath(); g.rect(x, y - size, w * frac, size * 2); g.clip();
      g.fillStyle = n.kind === "*" ? "#ffd24a" : "#6fb4ff";
      g.fillText(t, x, y);
      g.restore();
    }
    x += w;
  }
  g.textBaseline = "alphabetic";
}

function pill(g, x, y, w, h) {
  g.beginPath();
  g.roundRect ? g.roundRect(x, y, w, h, h / 2) : g.rect(x, y, w, h);
}
