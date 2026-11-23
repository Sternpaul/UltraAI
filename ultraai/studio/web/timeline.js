// Piano-roll timeline: waveform, singer pitch curve, notes, line breaks, Whisper words, issues.
import { isCont, lineEnd, lineStart, shown } from "./model.js";

const TOP = 22;          // break handles + line labels
const WORDS_H = 18;      // Whisper words band
const WAVE_H = 46;       // waveform band
const EDGE = 6;          // px: resize handle width

// custom properties use light-dark(): let the browser resolve them to a concrete colour
let probe;
function css(name) {
  if (!probe) { probe = document.createElement("span"); probe.hidden = true; document.body.append(probe); }
  probe.style.color = `var(${name})`;
  return getComputedStyle(probe).color;
}

export class Timeline {
  constructor(canvas, ed) {
    this.c = canvas;
    this.ed = ed;            // editor state (lines, selection, editor data, issues, view)
    this.t0 = 0;
    this.pps = 120;          // pixels per second
    this.colors = {};
    this.resize();
  }

  resize() {
    const r = this.c.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.w = Math.max(10, r.width);
    this.h = Math.max(10, r.height);
    this.c.width = Math.round(this.w * dpr);
    this.c.height = Math.round(this.h * dpr);
    this.ctx = this.c.getContext("2d");
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.colors = {
      bg: css("--panel"), bg2: css("--panel-2"), fg: css("--fg"), muted: css("--muted"), line: css("--line"),
      accent: css("--accent"), gold: css("--gold"), bad: css("--bad"), good: css("--good"), warn: css("--warn"),
    };
  }

  // ---- geometry ---------------------------------------------------------------------
  x(t) { return (t - this.t0) * this.pps; }
  t(x) { return this.t0 + x / this.pps; }
  get pitchTop() { return TOP + 4; }
  get pitchBottom() { return this.h - WORDS_H - WAVE_H - 6; }
  pitchRange() {
    const { lo, hi } = this.ed.pitchSpan;
    return [lo - 2, hi + 2];
  }
  rowH() { const [lo, hi] = this.pitchRange(); return (this.pitchBottom - this.pitchTop) / (hi - lo + 1); }
  y(p) { const [, hi] = this.pitchRange(); return this.pitchTop + (hi - p) * this.rowH(); }
  pitchAtY(y) { const [, hi] = this.pitchRange(); return Math.round(hi - (y - this.pitchTop) / this.rowH() - 0.5); }
  visible() { return [this.t0, this.t(this.w)]; }

  zoomAt(x, factor) {
    const t = this.t(x);
    this.pps = Math.min(1500, Math.max(8, this.pps * factor));
    this.t0 = Math.max(-1, t - x / this.pps);
  }

  ensureVisible(t, margin = 0.15) {
    const [a, b] = this.visible();
    const m = (b - a) * margin;
    if (t < a + m || t > b - m) this.t0 = Math.max(-1, t - (b - a) * 0.3);
  }

  // ---- hit testing --------------------------------------------------------------------
  hit(x, y) {
    const { lines } = this.ed;
    if (y < TOP) {
      for (let li = 0; li < lines.length; li++) {
        const b = lines[li].break;
        if (b !== null && b !== undefined && Math.abs(this.x(b) - x) <= 7) return { kind: "break", li };
      }
      return { kind: "empty" };
    }
    const rh = Math.max(6, this.rowH());
    for (let li = 0; li < lines.length; li++) {
      for (const n of lines[li].notes) {
        const x0 = this.x(n.start), x1 = this.x(n.end);
        if (x1 < x - EDGE || x0 > x + EDGE) continue;
        const y0 = this.y(n.pitch) - rh / 2;
        if (y < y0 - 3 || y > y0 + rh + 3) continue;
        const wide = x1 - x0 > 3 * EDGE;
        if (wide && Math.abs(x - x0) <= EDGE) return { kind: "start", n, li };
        if (wide && Math.abs(x - x1) <= EDGE) return { kind: "end", n, li };
        if (x >= x0 - 2 && x <= x1 + 2) return { kind: "note", n, li };
      }
    }
    return { kind: "empty" };
  }

  notesIn(xa, xb, ya, yb) {
    const out = [];
    const rh = Math.max(6, this.rowH());
    for (const ln of this.ed.lines) for (const n of ln.notes) {
      const x0 = this.x(n.start), x1 = this.x(n.end), y0 = this.y(n.pitch) - rh / 2;
      if (x1 >= xa && x0 <= xb && y0 + rh >= ya && y0 <= yb) out.push(n);
    }
    return out;
  }

  // ---- drawing ------------------------------------------------------------------------
  draw(now) {
    const g = this.ctx, C = this.colors, ed = this.ed;
    const [ta, tb] = this.visible();
    g.fillStyle = C.bg;
    g.fillRect(0, 0, this.w, this.h);

    // line bands (alternating) and line numbers
    ed.lines.forEach((ln, li) => {
      if (!ln.notes.length) return;
      const a = lineStart(ln), b = ln.break ?? lineEnd(ln);
      if (b < ta || a > tb) return;
      if (li % 2) { g.fillStyle = C.bg2; g.globalAlpha = 0.55; g.fillRect(this.x(a), TOP, this.x(b) - this.x(a), this.pitchBottom - TOP); g.globalAlpha = 1; }
      g.fillStyle = C.muted; g.font = "11px system-ui, sans-serif";
      g.fillText(String(li + 1), this.x(a) + 2, TOP + 11);
    });

    // semitone grid (octave C lines stronger)
    const [lo, hi] = this.pitchRange();
    for (let p = lo; p <= hi; p++) {
      const y = this.y(p);
      g.strokeStyle = C.line; g.globalAlpha = ((p % 12) + 12) % 12 === 0 ? 0.9 : 0.35; g.lineWidth = 1;
      g.beginPath(); g.moveTo(0, Math.round(y) + .5); g.lineTo(this.w, Math.round(y) + .5); g.stroke();
    }
    g.globalAlpha = 1;

    // issue regions
    for (const is of ed.issues) {
      if (is.dismissed || is.end < ta || is.start > tb) continue;
      g.fillStyle = C.bad;
      g.globalAlpha = is === ed.currentIssue ? 0.22 : 0.06 + 0.1 * is.risk;
      g.fillRect(this.x(is.start), TOP, Math.max(2, this.x(is.end) - this.x(is.start)), this.pitchBottom - TOP);
    }
    g.globalAlpha = 1;

    // loop region
    if (ed.loop) {
      g.fillStyle = C.accent; g.globalAlpha = 0.08;
      g.fillRect(this.x(ed.loop[0]), 0, this.x(ed.loop[1]) - this.x(ed.loop[0]), this.h);
      g.globalAlpha = 1;
    }

    // singer pitch curve
    const E = ed.editor;
    if (E?.pitch?.length) {
      const hop = E.pitch_hop, off = ed.curveOffset;
      g.strokeStyle = C.warn; g.lineWidth = 1.6; g.globalAlpha = 0.75;
      g.beginPath();
      let pen = false;
      const i0 = Math.max(0, Math.floor(ta / hop)), i1 = Math.min(E.pitch.length - 1, Math.ceil(tb / hop));
      for (let i = i0; i <= i1; i++) {
        const v = E.pitch[i];
        if (v === null || v === undefined) { pen = false; continue; }
        const x = this.x(i * hop), y = this.y(v - 60 + off);
        if (pen) g.lineTo(x, y); else g.moveTo(x, y);
        pen = true;
      }
      g.stroke(); g.globalAlpha = 1;
    }

    // notes
    const rh = Math.max(6, this.rowH());
    g.font = `${Math.min(13, Math.max(10, rh - 2))}px system-ui, sans-serif`;
    g.textBaseline = "middle";
    for (const ln of ed.lines) for (const n of ln.notes) {
      if (n.end < ta || n.start > tb) continue;
      const x0 = this.x(n.start), x1 = this.x(n.end), y0 = this.y(n.pitch) - rh / 2;
      const w = Math.max(2, x1 - x0);
      const sel = ed.selection.has(n);
      let fill = C.accent;
      if (n.kind === "*") fill = C.gold;
      else if (n.kind === "R" || n.kind === "G") fill = C.good;
      g.globalAlpha = n.kind === "F" ? 0.25 : (now >= n.start && now <= n.end ? 1 : 0.82);
      g.fillStyle = fill;
      roundRect(g, x0, y0, w, rh, Math.min(4, rh / 2));
      g.fill();
      g.globalAlpha = 1;
      if (n.kind === "F") { g.strokeStyle = C.muted; g.setLineDash([3, 3]); roundRect(g, x0, y0, w, rh, 3); g.stroke(); g.setLineDash([]); }
      if (sel) { g.strokeStyle = C.fg; g.lineWidth = 2; roundRect(g, x0 - 1, y0 - 1, w + 2, rh + 2, 4); g.stroke(); g.lineWidth = 1; }
      if (ed.extraNotes?.has(n)) {  // not in the lyrics
        g.strokeStyle = C.bad; g.lineWidth = 2; g.setLineDash([4, 3]);
        roundRect(g, x0 - 2, y0 - 2, w + 4, rh + 4, 4); g.stroke(); g.setLineDash([]); g.lineWidth = 1;
      }
      const label = isCont(n) ? shown(n).trim() : n.text.trim();  // melisma notes stay unlabelled
      if (label) {
        const tw = g.measureText(label).width;
        if (tw + 6 < w && rh >= 11) { g.fillStyle = "#fff"; g.fillText(label, x0 + 3, y0 + rh / 2); }
        else { g.fillStyle = C.fg; g.fillText(label, x0, y0 + rh + 8); }
      }
    }
    g.textBaseline = "alphabetic";

    // words of the lyrics missing in the song
    g.font = "bold 11px system-ui, sans-serif";
    for (const m of ed.missingMarks || []) {
      if (m.start < ta - 1 || m.start > tb) continue;
      const x = this.x(m.start) - 3, y = this.pitchBottom - 4;
      g.fillStyle = C.bad;
      g.beginPath(); g.moveTo(x, y - 10); g.lineTo(x - 6, y); g.lineTo(x + 6, y); g.closePath(); g.fill();
      g.fillText(`+ ${m.text.length > 24 ? m.text.slice(0, 23) + "…" : m.text}`, x + 8, y - 1);
    }

    // line breaks
    g.strokeStyle = C.muted; g.setLineDash([4, 4]);
    ed.lines.forEach((ln, li) => {
      const b = ln.break;
      if (b === null || b === undefined || b < ta || b > tb) return;
      const x = Math.round(this.x(b)) + .5;
      g.beginPath(); g.moveTo(x, TOP); g.lineTo(x, this.pitchBottom); g.stroke();
      g.setLineDash([]);
      g.fillStyle = ed.hoverBreak === li ? C.accent : C.muted;
      g.beginPath(); g.moveTo(x - 6, 2); g.lineTo(x + 6, 2); g.lineTo(x, 13); g.closePath(); g.fill();
      g.setLineDash([4, 4]);
    });
    g.setLineDash([]);

    // Whisper words band
    const wy = this.h - WAVE_H - WORDS_H;
    g.fillStyle = C.bg2; g.fillRect(0, wy, this.w, WORDS_H);
    if (E?.asr_words) {
      g.font = "11px system-ui, sans-serif"; g.textBaseline = "middle";
      let lastX = -1e9;
      for (const [a, , text, prob] of E.asr_words) {
        if (a < ta - 2 || a > tb) continue;
        const x = this.x(a);
        if (x < lastX + 2) continue;
        g.fillStyle = C.muted; g.globalAlpha = 0.45 + 0.55 * prob;
        g.fillText(text, x, wy + WORDS_H / 2);
        lastX = x + g.measureText(text).width;
      }
      g.globalAlpha = 1; g.textBaseline = "alphabetic";
    }

    // waveform
    const vy = this.h - WAVE_H;
    if (E?.peaks?.length) {
      const hop = E.peaks_hop;
      g.fillStyle = C.muted; g.globalAlpha = 0.6;
      for (let x = 0; x < this.w; x++) {
        const i0 = Math.floor(this.t(x) / hop), i1 = Math.max(i0 + 1, Math.floor(this.t(x + 1) / hop));
        let m = 0;
        for (let i = Math.max(0, i0); i < Math.min(E.peaks.length, i1); i++) m = Math.max(m, E.peaks[i]);
        const hh = (m / 255) * (WAVE_H - 4);
        g.fillRect(x, vy + (WAVE_H - hh) / 2, 1, Math.max(0.5, hh));
      }
      g.globalAlpha = 1;
    }

    // rubber band
    if (ed.band) {
      const [x0, y0, x1, y1] = ed.band;
      g.strokeStyle = C.accent; g.fillStyle = C.accent; g.globalAlpha = 0.12;
      g.fillRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));
      g.globalAlpha = 1; g.strokeRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));
    }

    // playhead
    const px = Math.round(this.x(now)) + .5;
    g.strokeStyle = C.bad; g.lineWidth = 1.5;
    g.beginPath(); g.moveTo(px, 0); g.lineTo(px, this.h); g.stroke(); g.lineWidth = 1;

    // time ruler ticks (top)
    g.fillStyle = C.muted; g.font = "10px system-ui, sans-serif";
    const step = niceStep(80 / this.pps);
    for (let t = Math.ceil(ta / step) * step; t < tb; t += step) {
      const x = this.x(t);
      g.fillRect(x, 0, 1, 4);
      if (t >= 0) g.fillText(fmtShort(t, step), x + 2, 10);
    }
  }
}

function roundRect(g, x, y, w, h, r) {
  g.beginPath();
  g.roundRect ? g.roundRect(x, y, w, h, r) : g.rect(x, y, w, h);
}

function niceStep(s) {
  for (const v of [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60]) if (v >= s) return v;
  return 120;
}
function fmtShort(t, step) {
  const m = Math.floor(t / 60), s = t - m * 60;
  return `${m}:${step < 1 ? s.toFixed(1).padStart(4, "0") : String(Math.round(s)).padStart(2, "0")}`;
}

// ---- overview strip: whole song, issues, current view ----------------------------------
export function drawOverview(canvas, ed, tl, now) {
  const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(r.width * dpr)) { canvas.width = Math.round(r.width * dpr); canvas.height = Math.round(r.height * dpr); }
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  const W = r.width, H = r.height, dur = ed.duration || 1, C = tl.colors;
  g.clearRect(0, 0, W, H);
  const E = ed.editor;
  if (E?.peaks?.length) {
    g.fillStyle = C.muted; g.globalAlpha = 0.45;
    const per = E.peaks.length / W;
    for (let x = 0; x < W; x++) {
      let m = 0;
      for (let i = Math.floor(x * per); i < Math.floor((x + 1) * per); i++) m = Math.max(m, E.peaks[i] || 0);
      const hh = (m / 255) * (H - 6);
      g.fillRect(x, (H - hh) / 2, 1, hh);
    }
    g.globalAlpha = 1;
  }
  for (const ln of ed.lines) {
    if (!ln.notes.length) continue;
    g.fillStyle = C.accent; g.globalAlpha = 0.6;
    g.fillRect(lineStart(ln) / dur * W, H - 5, Math.max(1, (lineEnd(ln) - lineStart(ln)) / dur * W), 3);
  }
  for (const is of ed.issues) {
    if (is.dismissed) continue;
    g.fillStyle = C.bad; g.globalAlpha = 0.4 + 0.6 * is.risk;
    g.fillRect(is.start / dur * W, 2, Math.max(2, (is.end - is.start) / dur * W), 6);
  }
  g.globalAlpha = 1;
  const [a, b] = tl.visible();
  g.strokeStyle = C.fg; g.lineWidth = 1.5;
  g.strokeRect(a / dur * W, 1, Math.max(4, (b - a) / dur * W), H - 2);
  g.fillStyle = C.bad; g.fillRect(now / dur * W, 0, 1.5, H);
}
