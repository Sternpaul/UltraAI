// Editor keymap: the registry of keyboard actions, their default keys, and per-browser
// overrides persisted to localStorage ("ultraai.keybinds.v1" -> {actionId: key}).
// Pure ESM: no DOM. Storage is injectable so the module is unit-testable under node.

export const STORAGE_KEY = "ultraai.keybinds.v1"; // gitleaks:allow (a localStorage key name, not a secret)

// Every keyboard action of the song editor. `key` is the default (rebindable) key,
// `also` lists extra keys that always trigger the action (kept from the original
// hardcoded map, e.g. Backspace alongside Delete, "=" alongside "+").
// Key strings: "Space", "Enter", "ArrowLeft", a single lowercase letter ("s"),
// "Shift+B", "Shift+Tab", or "Ctrl+Z". Tap-mode letter keys are NOT actions (global).
export const ACTIONS = [
  { id: "play", label: "Play / pause", key: "Space" },
  { id: "nudgeLeft", label: "Nudge selected notes left (Shift: 100 ms) / move playhead left", key: "ArrowLeft" },
  { id: "nudgeRight", label: "Nudge selected notes right (Shift: 100 ms) / move playhead right", key: "ArrowRight" },
  { id: "transposeUp", label: "Pitch up 1 semitone (Shift: octave)", key: "ArrowUp" },
  { id: "transposeDown", label: "Pitch down 1 semitone (Shift: octave)", key: "ArrowDown" },
  { id: "delete", label: "Delete selected notes", key: "Delete", also: ["Backspace"] },
  { id: "editLine", label: "Edit the current line's text", key: "Enter" },
  { id: "split", label: "Split the selected note", key: "s" },
  { id: "merge", label: "Merge the selected note with the next", key: "m" },
  { id: "normalOrNew", label: "Selection: make notes normal · nothing selected: new note at the playhead", key: "n" },
  { id: "snapPitch", label: "Set the selection's pitch to what is sung", key: "p" },
  { id: "startTap", label: "Tap mode: re-time the current line by tapping", key: "t" },
  { id: "kindGolden", label: "Golden note (press again: normal)", key: "g" },
  { id: "kindFreestyle", label: "Freestyle note (press again: normal)", key: "f" },
  { id: "kindRap", label: "Rap note (press again: normal)", key: "r" },
  { id: "breakLine", label: "Start a new line at the selected note", key: "b" },
  { id: "joinLine", label: "Join the line with the next", key: "Shift+B" },
  { id: "copyRepeats", label: "Copy this line's notes to all its repeats", key: "c" },
  { id: "toggleLink", label: "Link repeats on/off (edits apply to all copies)", key: "k" },
  { id: "loop", label: "Loop the selection / current issue", key: "l" },
  { id: "source", label: "Switch mix / vocals only", key: "v" },
  { id: "speedDown", label: "Slower playback", key: "[" },
  { id: "speedUp", label: "Faster playback", key: "]" },
  { id: "issueNext", label: "Next issue", key: "Tab" },
  { id: "issuePrev", label: "Previous issue", key: "Shift+Tab" },
  { id: "undo", label: "Undo", key: "Ctrl+Z" },
  { id: "redo", label: "Redo", key: "Ctrl+Y", also: ["Ctrl+Shift+Z", "Ctrl+Shift+Y"] },
  { id: "save", label: "Save now", key: "Ctrl+S", also: ["Ctrl+Shift+S"] },
  { id: "escape", label: "Clear selection / leave tap mode", key: "Escape" },
  { id: "help", label: "Show the shortcut help", key: "?" },
  { id: "zoomIn", label: "Zoom in", key: "+", also: ["="] },
  { id: "zoomOut", label: "Zoom out", key: "-" },
  { id: "home", label: "Go to the start of the song", key: "Home" },
];

export function actionById(id) {
  return ACTIONS.find(a => a.id === id) ?? null;
}

// ---------------------------------------------------------------- storage

function defaultStorage() {
  try { return globalThis.localStorage ?? null; } catch { return null; }
}

/** Validated overrides from storage: {actionId: key}. Unknown ids, non-strings and keys
 *  that would collide with another action's effective key/alias are dropped (see
 *  decide). */
export function load(storage = defaultStorage()) {
  let raw = null;
  try { raw = storage?.getItem(STORAGE_KEY); } catch { return {}; }
  if (!raw) return {};
  let obj;
  try { obj = JSON.parse(raw); } catch { return {}; }
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return {};
  return decide(obj).overrides;
}

/** Validate a raw {actionId: key} map against the defaults. First all overrides are
 *  applied to compute the *effective* keys — a default only counts as taken while its
 *  action still uses it, so a default freed by an overridden action can be claimed by
 *  another action's override — then, in registry order (never JSON order), overrides
 *  whose key collides with another action's effective key or always-on alias are
 *  dropped and their action falls back to its default; the earlier action in the
 *  registry wins a claimed key. A drop can reintroduce a collision via the fallback
 *  default, so this repeats until stable. */
function decide(raw) {
  const want = {};
  for (const [id, key] of Object.entries(raw ?? {})) {
    if (!actionById(id) || typeof key !== "string" || !key) continue;
    want[id] = key;
  }
  const index = new Map(ACTIONS.map((a, i) => [a.id, i]));
  const effective = new Map(ACTIONS.map(a => [a.id, want[a.id] ?? a.key]));
  const dropped = new Set();
  const live = id => want[id] !== undefined && !dropped.has(id);
  let changed = true;
  while (changed) {
    changed = false;
    for (const a of ACTIONS) {
      if (!live(a.id)) continue;
      const key = want[a.id];
      for (const b of ACTIONS) {
        if (b.id === a.id) continue;
        if (effective.get(b.id) !== key && !(b.also ?? []).includes(key)) continue;
        // b still uses the key on an override too: the later action in the registry
        // loses it; if b cannot move (default/alias), a's override is the problem.
        const loser = live(b.id) && index.get(b.id) > index.get(a.id) ? b.id : a.id;
        dropped.add(loser);
        effective.set(loser, actionById(loser).key);
        changed = true;
        if (loser === a.id) break;
      }
    }
  }
  const overrides = {};
  for (const [id, key] of Object.entries(want)) {
    if (!dropped.has(id)) overrides[id] = key;
  }
  return { overrides, effective };
}

export function save(overrides, storage = defaultStorage()) {
  try { storage?.setItem(STORAGE_KEY, JSON.stringify(overrides)); } catch { /* no storage */ }
}

export function reset(storage = defaultStorage()) {
  try { storage?.removeItem(STORAGE_KEY); } catch { /* no storage */ }
  return {};
}

// ---------------------------------------------------------------- key strings

/** Normalize a KeyboardEvent-like object to a canonical key string, or null when the
 *  event can never trigger an action (Alt combos, Ctrl+non-letter).
 *  Shift is part of the key only for letters and Tab; for arrows/space/Enter/... it is
 *  an argument of the action (bigger nudge, octave, ...), exactly as before. */
export function eventKey(e) {
  const ctrl = e.ctrlKey || e.metaKey;
  if (e.altKey && !ctrl) return null;
  const shift = e.shiftKey, k = e.key;
  if (ctrl) {
    if (typeof k !== "string" || k.length !== 1 || !/[a-z]/i.test(k)) return null;
    return (shift ? "Ctrl+Shift+" : "Ctrl+") + k.toUpperCase();
  }
  if (k === " ") return "Space";
  if (k.length === 1) {
    if (/[a-z]/i.test(k)) return shift ? "Shift+" + k.toUpperCase() : k.toLowerCase();
    return k;  // "+", "=", "-", "[", "]", "?", ... — Shift is already reflected in the character
  }
  if (shift && k === "Tab") return "Shift+Tab";
  return k;
}

/** Effective key of an action: the override if present, else the default. */
export function keyFor(actionId, overrides) {
  const a = actionById(actionId);
  if (!a) return null;
  const ov = overrides ?? load();
  return ov[actionId] ?? a.key;
}

/** Map an event to an action id (or null). Ctrl/Alt are ignored except for the
 *  Ctrl-bindings (undo/redo/save), which keep their modifiers. A Shift+<letter> that
 *  matches nothing falls back to the plain letter (legacy: Shift+S still splits a
 *  note); Shift+Tab does NOT fall back to Tab — Tab and Shift+Tab trigger only their
 *  own bound actions. */
export function resolve(e, overrides) {
  const k = eventKey(e);
  if (!k) return null;
  const ov = overrides ?? load();
  const hit = key => {
    if (!key) return null;
    for (const a of ACTIONS) {
      if ((ov[a.id] ?? a.key) === key || (a.also ?? []).includes(key)) return a.id;
    }
    return null;
  };
  const direct = hit(k);
  if (direct) return direct;
  if (k.startsWith("Shift+")) {
    const base = k.slice(6);
    if (base.length === 1 && /[a-z]/i.test(base)) return hit(base.toLowerCase());
  }
  return null;
}

/** All keys (effective + aliases) that collide between two different actions. */
export function findConflicts(overrides) {
  const ov = overrides ?? load();
  const byKey = new Map();
  for (const a of ACTIONS) {
    for (const key of [ov[a.id] ?? a.key, ...(a.also ?? [])]) {
      if (!byKey.has(key)) byKey.set(key, new Set());
      byKey.get(key).add(a.id);
    }
  }
  return [...byKey.entries()].filter(([, ids]) => ids.size > 1)
    .map(([key, ids]) => ({ key, actions: [...ids] }));
}

/** Rebind an action. key = null restores the default. Returns null on success, an
 *  {error} for an unusable key, or {conflict: otherActionId, key} when the key is
 *  already used by another action. Checks against the same effective-key validation
 *  as load(), so it can never store a state load() would prune. */
export function setBinding(actionId, key, storage = defaultStorage()) {
  const a = actionById(actionId);
  if (!a) return { error: "unknown-action" };
  const current = load(storage);
  if (key == null) {
    const cleared = { ...current };
    delete cleared[actionId];
    save(cleared, storage);
    return null;
  }
  if (typeof key !== "string" || !key) return { error: "invalid-key" };
  const { overrides, effective } = decide({ ...current, [actionId]: key });
  if (overrides[actionId] !== key) {
    const owner = ACTIONS.find(b => b.id !== actionId
      && (effective.get(b.id) === key || (b.also ?? []).includes(key)));
    return { conflict: owner?.id ?? null, key };
  }
  save(overrides, storage);
  return null;
}

// ---------------------------------------------------------------- capture validation

/** The set of keys a browser shortcut editor must refuse: they either make no sense as
 *  a binding (the bare modifier's own name) or the browser handles them itself, before
 *  any keydown handler can intercept. Choice: everything about tabs and windows
 *  (Ctrl+N/T/W and their Shift variants, Ctrl+Q), reload (Ctrl+R, F5) and devtools
 *  (F12 — interceptable in most setups but kept off the list as reserved). Editor
 *  defaults like Ctrl+Z/Y/S stay bindable: the page sees those fine. */
const BARE_MODIFIERS = new Set(["Shift", "Control", "Alt", "Meta"]);
export const BROWSER_RESERVED = new Set([
  "Ctrl+N", "Ctrl+Shift+N", "Ctrl+T", "Ctrl+Shift+T",
  "Ctrl+W", "Ctrl+Shift+W", "Ctrl+Q", "Ctrl+Shift+Q",
  "Ctrl+R", "Ctrl+Shift+R", "F5", "F12",
]);

/** Why a captured key press cannot become a binding, or null when it can:
 *  "modifier" — a bare Shift/Ctrl/Alt/Meta press (nothing to bind yet),
 *  "reserved" — a combo the browser keeps for itself (see BROWSER_RESERVED),
 *  "none"     — eventKey() finds no mappable key at all (e.g. Alt combos). */
export function captureProblem(e) {
  if (BARE_MODIFIERS.has(e.key)) return "modifier";
  const k = eventKey(e);
  if (!k) return "none";
  if (BROWSER_RESERVED.has(k)) return "reserved";
  return null;
}

// ---------------------------------------------------------------- display

const KEY_LABELS = {
  Space: "Space", ArrowLeft: "←", ArrowRight: "→", ArrowUp: "↑", ArrowDown: "↓",
  Delete: "Del", Backspace: "⌫", Enter: "Enter", Escape: "Esc", Tab: "Tab", Home: "Home",
};

/** Human-readable form of a key string for tooltips and the help dialog: " " -> "Space",
 *  "ArrowLeft" -> "←", "s" -> "S", "Shift+B" -> "Shift+B", "Ctrl+Z" -> "Ctrl+Z". */
export function keyLabel(key) {
  if (!key) return "";
  return key.split("+").map(part => {
    if (part.length === 1 && /[a-z]/.test(part)) return part.toUpperCase();
    return KEY_LABELS[part] ?? part;
  }).join("+");
}
