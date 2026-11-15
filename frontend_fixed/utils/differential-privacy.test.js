/**
 * Differential Privacy Utilities - Unit Tests
 * 运行方式: node differential-privacy.test.js
 */

import DP, { DifferentialPrivacy } from './differential-privacy.js';
const { PrivacyBudget } = DifferentialPrivacy;

let passed = 0;
let failed = 0;

function assert(condition, message) {
  if (condition) {
    passed++;
    console.log(`  PASS: ${message}`);
  } else {
    failed++;
    console.error(`  FAIL: ${message}`);
  }
}

function assertApprox(actual, expected, tolerance, message) {
  assert(Math.abs(actual - expected) <= tolerance, 
    `${message} (expected ${expected} ± ${tolerance}, got ${actual})`);
}

console.log('\n=== Differential Privacy Utilities Tests ===\n');

// Test 1: Laplace sample distribution
console.log('Test 1: Laplace sample distribution');
{
  const samples = Array.from({ length: 10000 }, () => DP._laplaceSample(1));
  const mean = samples.reduce((a, b) => a + b, 0) / samples.length;
  assertApprox(mean, 0, 0.1, 'Mean should be near 0');
  const stdDev = Math.sqrt(samples.reduce((a, b) => a + (b - mean) ** 2, 0) / samples.length);
  assertApprox(stdDev, Math.sqrt(2), 0.2, 'StdDev should be near sqrt(2) for b=1');
}

// Test 2: addNoise returns non-negative for count queries
console.log('\nTest 2: addNoise for count queries');
{
  for (let i = 0; i < 100; i++) {
    const result = DP.addNoise(50, 1.0, 'count');
    assert(result >= 0 && Number.isInteger(result), 
      `Count result should be non-negative integer (got ${result})`);
  }
}

// Test 3: anonymizeHistogram preserves total approximately
console.log('\nTest 3: Histogram anonymization');
{
  const histogram = { chemistry: 30, pharma: 25, materials: 15, energy: 10 };
  const anonymized = DP.anonymizeHistogram(histogram, 1.0);
  
  const originalTotal = Object.values(histogram).reduce((a, b) => a + b, 0);
  const anonTotal = Object.values(anonymized).reduce((a, b) => a + b, 0);
  
  // With 4 bins, epsilon per bin = 0.25, total noise std = sqrt(4) * sqrt(2)/0.25 ≈ 22.6
  // So error could be up to ~70 with 3-sigma, but usually within 30
  assertApprox(anonTotal, originalTotal, 40, 'Total should be approximately preserved');
  console.log(`    Original total: ${originalTotal}, Anonymized total: ${anonTotal}`);
  
  // All bins should be non-negative
  for (const [bin, val] of Object.entries(anonymized)) {
    assert(val >= 0, `Bin ${bin} should be non-negative (got ${val})`);
  }
}

// Test 4: anonymizeReport preserves structure
console.log('\nTest 4: Report anonymization');
{
  const report = {
    totalExperiments: 150,
    activeUsers: 42,
    newUsers: 8,
    paidConversions: 3,
    totalCreditsUsed: 2800,
    avgCreditsPerUser: 66.7,
    someStringField: 'preserved'
  };
  
  const anonymized = DP.anonymizeReport(report, 1.0);
  
  assert(anonymized._dp_applied === true, 'Should have _dp_applied flag');
  assert(anonymized._dp_epsilon === 1.0, 'Should record epsilon');
  assert(anonymized.someStringField === 'preserved', 'Non-numeric fields should be preserved');
  assert(typeof anonymized.totalExperiments === 'number', 'Numeric fields should remain numbers');
}

// Test 5: PrivacyBudget enforcement
console.log('\nTest 5: Privacy budget management');
{
  const budget = new PrivacyBudget(3.0);
  
  const r1 = budget.allocate('query1', 1.0);
  assert(r1.allowed === true && r1.allocated === 1.0 && r1.remaining === 2.0, 
    'First allocation should succeed');
  
  const r2 = budget.allocate('query2', 1.5);
  assert(r2.allowed === true && r2.allocated === 1.5 && r2.remaining === 0.5, 
    'Second allocation should succeed');
  
  const r3 = budget.allocate('query3', 1.0);
  assert(r3.allowed === true && r3.allocated === 0.5 && r3.remaining === 0.0, 
    'Third allocation should be capped at remaining');
  
  const r4 = budget.allocate('query4', 0.1);
  assert(r4.allowed === false && r4.allocated === 0, 
    'Fourth allocation should be denied (budget exhausted)');
  
  budget.reset();
  const r5 = budget.allocate('query5', 1.0);
  assert(r5.allowed === true && r5.remaining === 2.0, 'After reset, allocation should succeed');
}

// Test 6: minParticipantsForAccuracy
console.log('\nTest 6: Minimum participants calculation');
{
  const n1 = DP.minParticipantsForAccuracy(1.0, 1.0, 1);
  assert(n1 === 2, 'For eps=1, acc=1, sens=1, min n should be 2');
  
  const n2 = DP.minParticipantsForAccuracy(1.0, 0.1, 1);
  assert(n2 === 20, 'For eps=1, acc=0.1, sens=1, min n should be 20');
  
  const n3 = DP.minParticipantsForAccuracy(0.5, 0.1, 1);
  assert(n3 === 40, 'For eps=0.5, acc=0.1, sens=1, min n should be 40');
}

// Test 7: Edge cases
console.log('\nTest 7: Edge cases');
{
  assert(DP.addNoise(5, 0, 'count') === 5, 'Epsilon <= 0 should return trueValue');
  assert(DP.minParticipantsForAccuracy(0, 1, 1) === Infinity, 'Zero epsilon should return Infinity');
  assert(DP.minParticipantsForAccuracy(1, 0, 1) === Infinity, 'Zero accuracy should return Infinity');
}

// Summary
console.log(`\n=== Results: ${passed} passed, ${failed} failed ===\n`);
process.exit(failed > 0 ? 1 : 0);
