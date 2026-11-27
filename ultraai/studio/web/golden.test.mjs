// node --test ultraai/studio/web/golden.test.mjs — pure logic of the golden-notes state
// (no browser APIs needed: golden.js only imports model.js).
import test from "node:test";
import assert from "node:assert/strict";

import { PRESETS, nextGoldenState, normalizeGolden, goldenLabel, DEFAULT_GOLDEN } from "./golden.js";

const ids = PRESETS.map(p => p[0]);

test("preset apply sets preset and share", () => {
  const s = nextGoldenState(DEFAULT_GOLDEN, { type: "preset", preset: "hard", share: 0.04 });
  assert.deepEqual(s, { preset: "hard", share: 0.04 });
  const s2 = nextGoldenState(s, { type: "preset", preset: "mix", share: 0.08 });
  assert.deepEqual(s2, { preset: "mix", share: 0.08 });
});

test("every PRESETS id is accepted", () => {
  for (const id of ids) {
    assert.equal(nextGoldenState(null, { type: "preset", preset: id, share: 0.02 }).preset, id);
  }
});

test("manual toggle switches to custom and keeps the share", () => {
  const cur = { preset: "held", share: 0.06 };
  const s = nextGoldenState(cur, { type: "toggle" });
  assert.deepEqual(s, { preset: "custom", share: 0.06 });
  // toggling again stays custom, share still kept
  assert.deepEqual(nextGoldenState(s, { type: "toggle" }), { preset: "custom", share: 0.06 });
});

test("invalid preset is ignored (state unchanged)", () => {
  const cur = { preset: "high", share: 0.04 };
  assert.deepEqual(nextGoldenState(cur, { type: "preset", preset: "bogus", share: 0.2 }), cur);
  assert.deepEqual(nextGoldenState(cur, { type: "preset", preset: 42, share: 0.2 }), cur);
  assert.deepEqual(nextGoldenState(cur, { type: "nonsense" }), cur);
  assert.deepEqual(nextGoldenState(cur, null), cur);
});

test("bad share keeps the current share; share is clamped to the max", () => {
  const cur = { preset: "mix", share: 0.04 };
  assert.deepEqual(nextGoldenState(cur, { type: "preset", preset: "mix", share: NaN }), cur);
  assert.deepEqual(nextGoldenState(cur, { type: "preset", preset: "mix", share: -1 }), cur);
  assert.deepEqual(nextGoldenState(cur, { type: "preset", preset: "mix", share: 0.9 }), { preset: "mix", share: 0.5 });
});

test("normalizeGolden repairs junk from the server payload", () => {
  assert.deepEqual(normalizeGolden(undefined), DEFAULT_GOLDEN);
  assert.deepEqual(normalizeGolden({ preset: "nope", share: 99 }), DEFAULT_GOLDEN);
  assert.deepEqual(normalizeGolden({ preset: "custom", share: 0.06 }), { preset: "custom", share: 0.06 });
  assert.deepEqual(normalizeGolden({ preset: "none" }), { preset: "none", share: 0.04 });
});

test("label shows the preset and share, or custom", () => {
  assert.equal(goldenLabel({ preset: "hard", share: 0.04 }), "★ Hard jumps · 4%");
  assert.equal(goldenLabel({ preset: "mix", share: 0.06 }), "★ Mix · 6%");
  assert.equal(goldenLabel({ preset: "custom", share: 0.04 }), "★ custom");
  assert.equal(goldenLabel({ preset: "", share: 0.04 }), "★ –");
});
