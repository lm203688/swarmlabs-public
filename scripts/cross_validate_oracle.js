#!/usr/bin/env node
/**
 * Cross-validate JS Chebyshev vs Python Chebyshev oracle.
 * Reads test points (Python ground truth) and evaluates with JS, reporting max/mean error.
 */
const fs = require('fs');
const path = require('path');

const HERE = __dirname;
const ROOT = path.resolve(HERE, '..');
const POINTS_FILE = path.join(ROOT, 'scripts', 'oracle_cross_test_points.json');
const ORACLE_DIR = path.join(ROOT, 'frontend_fixed', 'reports', 'oracle');

// ---- JS Chebyshev (same as in [[path]].js) ----
function chebEval1D(coeffs, t) {
  const n = coeffs.length;
  if (n === 0) return 0;
  if (n === 1) return coeffs[0];
  if (n === 2) return coeffs[0] + coeffs[1] * t;
  let result = coeffs[0] + coeffs[1] * t;
  let tPrev = 1.0;
  let tCurr = t;
  for (let k = 2; k < n; k++) {
    const tNext = 2 * t * tCurr - tPrev;
    result += coeffs[k] * tNext;
    tPrev = tCurr;
    tCurr = tNext;
  }
  return result;
}

function chebEvalPoint(coeffs, dim, points) {
  if (dim === 1) return chebEval1D(coeffs, points[0]);
  if (dim === 2) {
    const deg0 = coeffs.length;
    const intermediates = new Array(deg0);
    for (let i = 0; i < deg0; i++) intermediates[i] = chebEval1D(coeffs[i], points[1]);
    return chebEval1D(intermediates, points[0]);
  }
  if (dim === 3) {
    const deg0 = coeffs.length;
    const deg1 = coeffs[0].length;
    const plane2D = [];
    for (let i = 0; i < deg0; i++) {
      const row2D = new Array(deg1);
      for (let j = 0; j < deg1; j++) row2D[j] = chebEval1D(coeffs[i][j], points[2]);
      plane2D.push(row2D);
    }
    const line1D = new Array(deg0);
    for (let i = 0; i < deg0; i++) line1D[i] = chebEval1D(plane2D[i], points[1]);
    return chebEval1D(line1D, points[0]);
  }
  return NaN;
}

function rescaleToCheb(x, lo, hi) {
  const mid = 0.5 * (lo + hi);
  const span = 0.5 * (hi - lo);
  if (span < 1e-15) return 0;
  return (x - mid) / span;
}

// ---- Load test points ----
const testData = JSON.parse(fs.readFileSync(POINTS_FILE, 'utf-8'));
const points = testData.points;
console.log(`Loaded ${points.length} test points`);

// ---- Load oracle coefficients (cache by scenario) ----
const oracleCache = {};
function loadOracle(key) {
  if (oracleCache[key]) return oracleCache[key];
  const fp = path.join(ORACLE_DIR, `${key}.json`);
  if (!fs.existsSync(fp)) return null;
  const data = JSON.parse(fs.readFileSync(fp, 'utf-8'));
  const inner = data.data || data;
  oracleCache[key] = {
    dim: inner.dim,
    bounds: inner.bounds,
    coefficients: inner.coefficients
  };
  return oracleCache[key];
}

// ---- Evaluate and compare ----
const results = {};
let globalMaxErr = 0;
let globalMeanErr = 0;
let totalPoints = 0;

for (const pt of points) {
  const key = pt.scenario_key;
  const oracle = loadOracle(key);
  if (!oracle) { continue; }

  const pts = pt.x.map((v, d) => {
    const b = oracle.bounds[d];
    return rescaleToCheb(v, b[0], b[1]);
  });
  const y_js = chebEvalPoint(oracle.coefficients, oracle.dim, pts);

  const y_py = pt.y_true;
  const absErr = Math.abs(y_js - y_py);
  const relErr = y_py !== 0 ? absErr / Math.abs(y_py) : 0;

  if (!results[key]) results[key] = { max_abs: 0, max_rel: 0, sum_abs: 0, n: 0 };
  results[key].max_abs = Math.max(results[key].max_abs, absErr);
  results[key].max_rel = Math.max(results[key].max_rel, relErr);
  results[key].sum_abs += absErr;
  results[key].n++;

  globalMaxErr = Math.max(globalMaxErr, absErr);
  globalMeanErr += absErr;
  totalPoints++;
}

// ---- Report ----
console.log(`\n=== Cross-Validation Results ===`);
console.log(`Total points: ${totalPoints}`);
console.log(`Global max abs error: ${globalMaxErr.toExponential(4)}`);
console.log(`Global mean abs error: ${(globalMeanErr / totalPoints).toExponential(4)}`);
console.log('');

const sorted = Object.entries(results).sort((a, b) => b[1].max_abs - a[1].max_abs);
console.log('Per-scenario errors (sorted by max_abs desc):');
console.log('scenario'.padEnd(35), 'dim', 'n', 'max_abs_err'.padEnd(20), 'max_rel_err');
console.log('-'.repeat(95));
for (const [key, r] of sorted) {
  console.log(
    key.padEnd(35),
    String(oracleCache[key] ? oracleCache[key].dim : '?'),
    String(r.n).padEnd(3),
    r.max_abs.toExponential(4).padEnd(20),
    r.max_rel.toExponential(4)
  );
}

// ---- Verdict ----
const PASS_THRESHOLD = 1e-5;
const failCount = sorted.filter(([_, r]) => r.max_abs > PASS_THRESHOLD).length;
if (failCount === 0) {
  console.log(`\n✓ ALL PASS: max abs error < ${PASS_THRESHOLD}`);
  process.exit(0);
} else {
  console.log(`\n✗ ${failCount} scenario(s) exceed threshold ${PASS_THRESHOLD}`);
  for (const [key, r] of sorted) {
    if (r.max_abs > PASS_THRESHOLD) {
      console.log(`  FAIL: ${key} max_abs=${r.max_abs.toExponential(4)}`);
    }
  }
  process.exit(1);
}