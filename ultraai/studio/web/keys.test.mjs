// Unit tests for the pure keymap module (keys.js) — run with: node --test web/keys.test.mjs
import assert from "node:assert/strict";
import test from "node:test";

import {
  ACTIONS, STORAGE_KEY, actionById, captureProblem, eventKey, findConflicts, keyFor, keyLabel, load, reset, resolve, save, setBinding,
} from "./keys.js";

// Minimal localStorage stand-in (synthetic data only).
function memStorage() {
  const m = new Map();
  return {
    getItem: k => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: k => m.delete(k),
  };
}

const ev = (key, mods = {}) => ({ key, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...mods });

test("every action has a unique id, a label and a non-empty default key", () => {
  const ids = new Set();
  for (const a of ACTIONS) {
    assert.ok(a.id && a.label && a.key, `complete entry: ${JSON.stringify(a)}`);
    assert.ok(!ids.has(a.id), `duplicate id ${a.id}`);
    ids.add(a.id);
  }
});

test("default keybinds resolve to their actions", () => {
  const cases = [
    [ev(" "), "play"],
    [ev("ArrowLeft"), "nudgeLeft"],
    [ev("ArrowRight"), "nudgeRight"],
    [ev("ArrowUp"), "transposeUp"],
    [ev("ArrowDown"), "transposeDown"],
    [ev("Delete"), "delete"],
    [ev("Backspace"), "delete"],          // alias kept from the hardcoded map
    [ev("Enter"), "editLine"],
    [ev("s"), "split"], [ev("m"), "merge"], [ev("n"), "normalOrNew"], [ev("p"), "snapPitch"], [ev("t"), "startTap"],
    [ev("g"), "kindGolden"], [ev("f"), "kindFreestyle"], [ev("r"), "kindRap"],
    [ev("b"), "breakLine"], [ev("B", { shiftKey: true }), "joinLine"],
    [ev("c"), "copyRepeats"], [ev("k"), "toggleLink"], [ev("l"), "loop"], [ev("v"), "source"],
    [ev("["), "speedDown"], [ev("]"), "speedUp"],
    [ev("Tab"), "issueNext"], [ev("Tab", { shiftKey: true }), "issuePrev"],
    [ev("z", { ctrlKey: true }), "undo"],
    [ev("y", { ctrlKey: true }), "redo"],
    [ev("Z", { ctrlKey: true, shiftKey: true }), "redo"],   // Ctrl+Shift+Z = redo, as before
    [ev("s", { ctrlKey: true }), "save"],
    [ev("Escape"), "escape"], [ev("?"), "help"],
    [ev("+"), "zoomIn"], [ev("="), "zoomIn"],                // "=" alias kept
    [ev("-"), "zoomOut"], [ev("Home"), "home"],
  ];
  for (const [e, want] of cases) {
    assert.equal(resolve(e, {}), want, `resolve(${JSON.stringify(e)})`);
  }
});

test("defaults carry no conflicts", () => {
  assert.deepEqual(findConflicts({}), []);
});

test("modifier handling: alt ignored, meta = ctrl, shift is an argument for arrows/space", () => {
  assert.equal(resolve(ev("z", { altKey: true }), {}), null);          // Alt+Z: nothing
  assert.equal(resolve(ev("z", { metaKey: true }), {}), "undo");        // Cmd+Z on macOS
  assert.equal(resolve(ev("z", { ctrlKey: true, altKey: true }), {}), "undo");  // Ctrl wins, as before
  assert.equal(resolve(ev("r", { ctrlKey: true }), {}), null);          // unbound ctrl combo
  assert.equal(resolve(ev("ArrowLeft", { shiftKey: true }), {}), "nudgeLeft"); // Shift = 100 ms, same action
  assert.equal(resolve(ev(" "), {}), "play");
  assert.equal(resolve(ev(" ", { shiftKey: true }), {}), "play");       // Shift+Space still plays
  assert.equal(resolve(ev("S", { shiftKey: true }), {}), "split");      // legacy: Shift+S fell back to "s"
  assert.equal(resolve(ev("B"), {}), "breakLine");                      // caps-lock B = b, as before
  assert.equal(resolve(ev("x"), {}), null);
});

test("eventKey normalization", () => {
  assert.equal(eventKey(ev(" ")), "Space");
  assert.equal(eventKey(ev("b")), "b");
  assert.equal(eventKey(ev("B", { shiftKey: true })), "Shift+B");
  assert.equal(eventKey(ev("B")), "b");
  assert.equal(eventKey(ev("?", { shiftKey: true })), "?");   // shift already in the character
  assert.equal(eventKey(ev("Tab", { shiftKey: true })), "Shift+Tab");
  assert.equal(eventKey(ev("Enter", { shiftKey: true })), "Enter");
  assert.equal(eventKey(ev("z", { ctrlKey: true })), "Ctrl+Z");
  assert.equal(eventKey(ev("Z", { ctrlKey: true, shiftKey: true })), "Ctrl+Shift+Z");
  assert.equal(eventKey(ev("z", { metaKey: true })), "Ctrl+Z");
  assert.equal(eventKey(ev("z", { altKey: true })), null);
  assert.equal(eventKey(ev("F5", { ctrlKey: true })), null);  // ctrl + non-letter: unbindable
});

test("override roundtrip through storage", () => {
  const st = memStorage();
  assert.equal(keyFor("play", load(st)), "Space");
  assert.equal(setBinding("play", "x", st), null);
  assert.equal(keyFor("play", load(st)), "x");
  assert.equal(resolve(ev("x"), load(st)), "play");
  assert.equal(resolve(ev(" "), load(st)), null);              // old key no longer bound
  assert.deepEqual(JSON.parse(st.getItem(STORAGE_KEY)), { play: "x" });
  // a fresh load from the same storage sees the override (persistence)
  assert.deepEqual(load(st), { play: "x" });
  // explicit save/load roundtrip (save replaces the whole map)
  save({ split: "q" }, st);
  assert.deepEqual(load(st), { split: "q" });
  save({ play: "x", split: "q" }, st);
  assert.deepEqual(load(st), { play: "x", split: "q" });
});

test("conflict detection rejects a key used by another action (incl. aliases)", () => {
  const st = memStorage();
  assert.deepEqual(setBinding("play", "s", st), { conflict: "split", key: "s" });
  assert.equal(keyFor("play", load(st)), "Space");              // unchanged after rejection
  assert.deepEqual(setBinding("play", "Backspace", st), { conflict: "delete", key: "Backspace" });
  assert.deepEqual(setBinding("loop", "=", st), { conflict: "zoomIn", key: "=" });
  assert.deepEqual(setBinding("play", "Ctrl+Z", st), { conflict: "undo", key: "Ctrl+Z" });
  assert.equal(setBinding("play", "x", st), null);              // free key works
  assert.deepEqual(findConflicts(load(st)), []);                // stored state stays consistent
  // findConflicts reports collisions in a raw override map
  const cs = findConflicts({ play: "s" });
  assert.equal(cs.length, 1);
  assert.equal(cs[0].key, "s");
  assert.deepEqual(new Set(cs[0].actions), new Set(["play", "split"]));
});

test("rebinding one action does not disturb the other defaults", () => {
  const st = memStorage();
  setBinding("help", "h", st);
  const ov = load(st);
  assert.equal(keyFor("help", ov), "h");
  assert.equal(keyFor("split", ov), "s");
  assert.equal(resolve(ev("?"), ov), null);
  assert.equal(resolve(ev("h"), ov), "help");
  for (const a of ACTIONS) {
    if (a.id === "help") continue;
    assert.equal(keyFor(a.id, ov), a.key, `${a.id} untouched`);
  }
});

test("reset restores all defaults and clears storage", () => {
  const st = memStorage();
  setBinding("play", "x", st);
  setBinding("split", "q", st);
  assert.notDeepEqual(load(st), {});
  reset(st);
  assert.equal(st.getItem(STORAGE_KEY), null);
  assert.deepEqual(load(st), {});
  assert.equal(keyFor("play", load(st)), "Space");
  assert.equal(resolve(ev(" "), load(st)), "play");
  assert.equal(resolve(ev("x"), load(st)), null);
});

test("setBinding(id, null) restores a single default", () => {
  const st = memStorage();
  setBinding("delete", "x", st);
  assert.equal(keyFor("delete", load(st)), "x");
  assert.equal(setBinding("delete", null, st), null);
  assert.equal(keyFor("delete", load(st)), "Delete");
  assert.equal(resolve(ev("Delete"), load(st)), "delete");
  assert.equal(resolve(ev("Backspace"), load(st)), "delete");  // alias still active
});

test("a default freed by another action's override can be claimed (setBinding -> reload roundtrip)", () => {
  const st = memStorage();
  assert.equal(setBinding("split", "x", st), null);
  assert.equal(setBinding("merge", "s", st), null);  // "s" was freed by split's override
  const ov = load(st);  // fresh load, as after a page reload
  assert.deepEqual(ov, { split: "x", merge: "s" });  // the review repro: "s" must survive
  assert.equal(keyFor("split", ov), "x");
  assert.equal(keyFor("merge", ov), "s");
  assert.equal(resolve(ev("s"), ov), "merge");       // "s" fires merge, not nothing
  assert.equal(resolve(ev("x"), ov), "split");
  assert.equal(resolve(ev("m"), ov), null);          // merge's default is no longer bound
  // deterministic: the same stored state in reverse JSON order loads identically
  st.setItem(STORAGE_KEY, JSON.stringify({ merge: "s", split: "x" }));
  assert.deepEqual(load(st), { split: "x", merge: "s" });
});

test("load ignores garbage: malformed JSON, unknown ids, non-strings, stored conflicts", () => {
  const st = memStorage();
  st.setItem(STORAGE_KEY, "not json{");
  assert.deepEqual(load(st), {});
  st.setItem(STORAGE_KEY, JSON.stringify([1, 2]));
  assert.deepEqual(load(st), {});
  st.setItem(STORAGE_KEY, JSON.stringify({ nope: "x", play: 42, split: "", zoomIn: "-" }));
  assert.deepEqual(load(st), {});                               // "-" belongs to zoomOut -> dropped
  st.setItem(STORAGE_KEY, JSON.stringify({ play: "s" }));       // "s" is split's effective default
  assert.deepEqual(load(st), {});
  st.setItem(STORAGE_KEY, JSON.stringify({ play: "s", split: "q" }));
  // split moved to "q", so its default "s" is free — this pair is conflict-free now
  assert.deepEqual(load(st), { play: "s", split: "q" });
});

test("keyLabel renders keys for tooltips and help", () => {
  assert.equal(keyLabel("Space"), "Space");
  assert.equal(keyLabel("ArrowLeft"), "←");
  assert.equal(keyLabel("Delete"), "Del");
  assert.equal(keyLabel("Backspace"), "⌫");
  assert.equal(keyLabel("s"), "S");
  assert.equal(keyLabel("Shift+B"), "Shift+B");
  assert.equal(keyLabel("Ctrl+Z"), "Ctrl+Z");
  assert.equal(keyLabel("?"), "?");
  assert.equal(keyLabel("+"), "+");
  assert.equal(keyLabel(""), "");
});

test("actionById and registry sanity", () => {
  assert.equal(actionById("play").key, "Space");
  assert.equal(actionById("does-not-exist"), null);
  assert.equal(keyFor("does-not-exist", {}), null);
  assert.ok(ACTIONS.length >= 30, "the registry covers every keyboard action of the editor");
});

test("legacy Shift+letter fallback is letters-only: Shift+Tab does not become Tab", () => {
  assert.equal(resolve(ev("S", { shiftKey: true }), {}), "split");          // legacy fallback
  assert.equal(resolve(ev("Tab", { shiftKey: true }), {}), "issuePrev");   // its own binding
  const st = memStorage();
  assert.equal(setBinding("issuePrev", "q", st), null);
  const ov = load(st);
  assert.equal(resolve(ev("q", { shiftKey: true }), ov), "issuePrev");     // Shift+Q falls back like Shift+S
  assert.equal(resolve(ev("Tab", { shiftKey: true }), ov), null);          // no fallback once unbound
  assert.equal(resolve(ev("Tab"), ov), "issueNext");                       // Tab still its own action
});

test("capture rejects bare modifiers, browser-reserved combos and unbindable keys", () => {
  assert.equal(captureProblem(ev("Shift", { shiftKey: true })), "modifier");
  assert.equal(captureProblem(ev("Control", { ctrlKey: true })), "modifier");
  assert.equal(captureProblem(ev("Alt", { altKey: true })), "modifier");
  assert.equal(captureProblem(ev("Meta", { metaKey: true })), "modifier");
  assert.equal(captureProblem(ev("w", { ctrlKey: true })), "reserved");              // closes the tab
  assert.equal(captureProblem(ev("T", { ctrlKey: true, shiftKey: true })), "reserved");
  assert.equal(captureProblem(ev("N", { ctrlKey: true, shiftKey: true })), "reserved");
  assert.equal(captureProblem(ev("F5")), "reserved");
  assert.equal(captureProblem(ev("F12")), "reserved");
  assert.equal(captureProblem(ev("x", { altKey: true })), "none");                  // Alt: never a binding
  assert.equal(captureProblem(ev("z", { ctrlKey: true })), null);                   // editor default
  assert.equal(captureProblem(ev("Tab", { shiftKey: true })), null);                // bindable combo
  assert.equal(captureProblem(ev("x")), null);
});
