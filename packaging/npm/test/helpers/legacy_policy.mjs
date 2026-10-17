// The dispatch defaults before 2026-10-17 (accept on the check alone, one model at a time, a 150 s limit, 2 runs per
// provider, 3 models, +240 s for the strong model, the per-model cap and the area skip on). The tests of one mechanism
// (undo, the cap, the latency fixes, a plan in parts, ...) were written against them and set them explicitly, so they
// keep testing that mechanism; the current defaults have their own tests (test/defaults.test.mjs).
export const LEGACY_POLICY = Object.freeze({
  STEALTHLAB_DISPATCH_ACCEPT: "check", STEALTHLAB_DISPATCH_PARALLEL: "off", STEALTHLAB_DISPATCH_TIMEOUT_S: "150",
  STEALTHLAB_DISPATCH_MAX_PARALLEL: "2", STEALTHLAB_DISPATCH_MAX_RUNGS: "3", STEALTHLAB_DISPATCH_STRONG_EXTRA_S: "240",
  STEALTHLAB_DISPATCH_MODEL_CAP: "on", STEALTHLAB_DISPATCH_AREA_SKIP: "on",
});
