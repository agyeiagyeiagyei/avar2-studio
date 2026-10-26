/**
 * S1(c): the Preview's coordinate mapping. Run with
 *   node e2e/preview-coords.spec.mjs
 *
 * Lives under e2e/ with the other node specs: under src/ the eslint config
 * treats it as browser code, where `console`/`process` are undefined
 * (no-undef — the one lint error that stood since 5527ecc).
 */
import assert from 'node:assert/strict';
import { previewRenderCoords, variationSettings } from '../src/preview-coords.js';

const axes = [
  { tag: 'wght', default: 400 },
  { tag: 'XTRA', default: 663.1 },
  { tag: 'XOPQ', default: 202.5 },
];
const userAxes = [axes[0]];
const parametricAxes = [axes[1], axes[2]];
const mapped = { XTRA: 700.0, XOPQ: 210.0 };

// Healthy build: inputs pass straight through (the font's avar2 does the work).
const healthy = previewRenderCoords({
  degraded: false,
  effectiveCoords: { wght: 900, XTRA: 663.1, XOPQ: 202.5 },
  userAxes, parametricAxes, mappedParams: mapped, overrides: new Set(),
});
assert.deepEqual(healthy, { wght: 900, XTRA: 663.1, XOPQ: 202.5 });

// Degraded build: parametric axes pinned at the oracle, user axes at defaults.
const degraded = previewRenderCoords({
  degraded: true,
  effectiveCoords: { wght: 900, XTRA: 663.1, XOPQ: 202.5 },
  userAxes, parametricAxes, mappedParams: mapped, overrides: new Set(),
});
assert.equal(degraded.wght, 400, 'user axes held at their defaults');
assert.equal(degraded.XTRA, 700.0, 'parametric axis pinned at the oracle');
assert.equal(degraded.XOPQ, 210.0);

// Degraded build + an explicit override: the override wins (round-11 review).
const overridden = previewRenderCoords({
  degraded: true,
  effectiveCoords: { wght: 900, XTRA: 663.1, XOPQ: 555.0 },
  userAxes, parametricAxes, mappedParams: mapped,
  overrides: new Set(['XOPQ']),
});
assert.equal(overridden.XOPQ, 555.0, 'a dragged parametric axis keeps its value');
assert.equal(overridden.XTRA, 700.0, 'the others stay pinned');

// No oracle for an axis: fall back to the input rather than pinning NaN.
const noOracle = previewRenderCoords({
  degraded: true,
  effectiveCoords: { wght: 900, XTRA: 663.1, XOPQ: 202.5 },
  userAxes, parametricAxes, mappedParams: { XTRA: 700.0 }, overrides: new Set(),
});
assert.equal(noOracle.XOPQ, 202.5);

// The settings string keeps the exact fvar tag case.
assert.equal(
  variationSettings(axes, healthy),
  '"wght" 900, "XTRA" 663.1, "XOPQ" 202.5',
);

console.log('preview-coords: all passed');
