#!/usr/bin/env node
/**
 * fix_dissolution_usp.js — 修复 modelDissolutionUSP 的真实建模缺陷。
 *
 * 原实现的问题（均为真实缺陷，非容差问题）:
 *   1. timePoints 硬编码 [0.5,1,2,4,6,8]，完全忽略调用方传入的 time_points_h；
 *   2. 不区分速释(IR)/缓释(ER)剂型，k=0.18、n=0.65 恒定 —— 导致阿司匹林速释片
 *      8 小时溶出仅 72%，药剂学上不成立（IR 片 45min 应 ≥80%，典型 85~95%）;
 *   3. rpm / temperature_C / medium_volume_ml 读入后从未进入动力学计算;
 *   4. 无漏槽条件(sink)判定，低溶解度药物不受限制。
 *
 * 修复依据:
 *   - USP <711> Dissolution: 速释片验收准则 Q=80% @ 45 min;
 *   - Levich 方程: 扩散边界层厚度 ∝ rpm^-0.5，故 k ∝ rpm^0.5;
 *   - Arrhenius: 溶出过程表观活化能约 20 kJ/mol;
 *   - Weibull 释放模型: Mt/M∞ = 1 - exp(-(k·t)^n)，IR 片 t50 ≈ 12 min。
 */
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, '..', 'frontend_fixed', 'functions', 'api', '[[path]].js');
const s = fs.readFileSync(SRC, 'utf8');

const start = s.indexOf('function modelDissolutionUSP');
if (start < 0) { console.error('找不到 modelDissolutionUSP'); process.exit(1); }
let depth = 0, end = -1;
for (let j = s.indexOf('{', start); j < s.length; j++) {
  if (s[j] === '{') depth++;
  else if (s[j] === '}') { depth--; if (depth === 0) { end = j + 1; break; } }
}
if (end < 0) { console.error('无法定位函数结尾'); process.exit(1); }

const oldFn = s.slice(start, end);

const newFn = `function modelDissolutionUSP(engine, p, stats) {
  const T = getParam(p, ['temperature_C'], 37);
  const rpm = getParam(p, ['rpm'], 50);
  const vol = getParam(p, ['medium_volume_ml'], 900);
  const qVal = getParam(p, ['q_value'], 80);
  const apparatus = p.apparatus_type || 'II (Paddle)';

  // [FIX-USP-01] 尊重调用方传入的时间点。USP 溶出实践常以「分钟」给点，
  //              若最大值 > 24 则判定为分钟并自动换算为小时。
  let rawTP = getParam(p, ['time_points_h', 'time_points', 'time_points_min'], null);
  let timePoints, timeUnitNote;
  if (Array.isArray(rawTP) && rawTP.length) {
    const nums = rawTP.map(Number).filter(function (x) { return isFinite(x) && x >= 0; });
    const maxT = nums.length ? Math.max.apply(null, nums) : 0;
    if (maxT > 24) { timePoints = nums.map(function (t) { return t / 60; }); timeUnitNote = 'min -> h (auto-detected: max > 24)'; }
    else { timePoints = nums; timeUnitNote = 'h (as provided)'; }
  } else {
    timePoints = [0.25, 0.5, 0.75, 1, 2, 4]; timeUnitNote = 'h (USP default grid)';
  }
  timePoints = timePoints.filter(function (t) { return isFinite(t) && t > 0; }).sort(function (a, b) { return a - b; });
  if (!timePoints.length) timePoints = [0.25, 0.5, 0.75, 1, 2, 4];

  // [FIX-USP-02] 剂型判定：存在缓释聚合物 -> ER 骨架片；否则 IR 速释片。
  //              IR: t50 ≈ 12 min (Weibull k≈3.25/h, n≈0.85)
  //              ER: 骨架扩散控制 (k≈0.20/h, n≈0.65)
  const polymerName = String(getParam(p, ['polymer_name'], 'none') || 'none').toLowerCase().trim();
  const polyConc = Number(getParam(p, ['polymer_concentration'], 0)) || 0;
  const isER = polyConc > 0 && !/^(none|no|nil|null|n\\/a|-)$/.test(polymerName);
  const k_base = isER ? 0.20 : 3.25;
  const n_base = isER ? 0.65 : 0.85;

  // [FIX-USP-03] 水动力学 (Levich)：扩散边界层 δ ∝ rpm^-0.5，故 k ∝ rpm^0.5
  const rpmFactor = Math.pow(Math.max(rpm, 1) / 50, 0.5);
  // [FIX-USP-04] 温度 (Arrhenius)：溶出表观活化能约 20 kJ/mol，参考态 37°C
  const tempFactor = Math.exp(-(20000 / 8.314) * (1 / (T + 273.15) - 1 / 310.15));
  // [FIX-USP-05] 漏槽条件 (sink)：Cs·V/Dose ≥ 3 视为满足，否则释放受溶解度限制
  const drugSolubility = getParam(p, ['drug_solubility_mg_ml', 'solubility_mg_ml'], 3.0);
  const dose = getParam(p, ['dose_mg', 'drug_load_mg'], 100);
  const sinkRatio = (drugSolubility * vol) / Math.max(dose, 1);
  const sinkFactor = sinkRatio >= 3 ? 1.0 : Math.max(0.3, Math.pow(sinkRatio / 3, 0.5));

  const k_eff = k_base * rpmFactor * tempFactor * sinkFactor;

  const nTablets = 12;
  const tabletResults = [];
  for (let tab = 0; tab < nTablets; tab++) {
    // 片间差异：USP <711> 允许的片间 RSD，k 与 n 各自小幅扰动
    const kTab = k_eff * (1 + normalRandom(0, 0.06));
    const nTab = n_base + normalRandom(0, 0.025);
    const profile = timePoints.map(function (t) {
      const release = 100 * (1 - Math.exp(-Math.pow(Math.max(kTab, 1e-6) * t, Math.max(nTab, 0.1))));
      return { time_h: round4(t), release_pct: round2(Math.max(0, Math.min(100, release + normalRandom(0, 0.8)))) };
    });
    tabletResults.push(profile);
  }
  const meanByTime = timePoints.map(function (t, i) {
    const vals = tabletResults.map(function (tr) { return tr[i].release_pct; });
    const mean = vals.reduce(function (a, b) { return a + b; }, 0) / vals.length;
    const std = Math.sqrt(vals.reduce(function (sq, v) { return sq + Math.pow(v - mean, 2); }, 0) / vals.length);
    return { time_h: round4(t), mean_release_pct: round2(mean), rsd_pct: round2(mean > 0 ? (std / mean) * 100 : 0) };
  });

  // [FIX-USP-06] Q 判定时间：IR 默认 45 min (0.75 h)，ER 默认 2 h；支持线性插值取点
  const qTime = getParam(p, ['q_time_h'], isER ? 2 : 0.75);
  let qRelease = null;
  const exact = meanByTime.find(function (pt) { return Math.abs(pt.time_h - qTime) < 1e-6; });
  if (exact) qRelease = exact.mean_release_pct;
  else {
    for (let i = 1; i < meanByTime.length; i++) {
      const a = meanByTime[i - 1], b = meanByTime[i];
      if (qTime >= a.time_h && qTime <= b.time_h) {
        const w = (qTime - a.time_h) / Math.max(b.time_h - a.time_h, 1e-9);
        qRelease = round2(a.mean_release_pct + w * (b.mean_release_pct - a.mean_release_pct));
        break;
      }
    }
    if (qRelease === null && meanByTime.length) {
      qRelease = qTime > meanByTime[meanByTime.length - 1].time_h
        ? meanByTime[meanByTime.length - 1].mean_release_pct
        : meanByTime[0].mean_release_pct;
    }
  }
  const passesQ = qRelease !== null && qRelease >= qVal;

  return {
    result: round2(meanByTime[meanByTime.length - 1].mean_release_pct),
    model: isER
      ? 'USP <711> Multi-Tablet Dissolution (ER matrix, Weibull + Levich hydrodynamics)'
      : 'USP <711> Multi-Tablet Dissolution (IR tablet, Weibull + Levich hydrodynamics)',
    model_details: {
      n_tablets: nTablets, apparatus: apparatus, medium_volume_ml: vol, rpm: rpm,
      dosage_form: isER ? 'extended-release (polymer matrix)' : 'immediate-release',
      k_base: round4(k_base), n_base: round3(n_base), k_effective: round4(k_eff),
      rpm_factor_levich: round3(rpmFactor), temp_factor_arrhenius: round3(tempFactor),
      sink_ratio: round2(sinkRatio), sink_factor: round3(sinkFactor),
      time_unit_handling: timeUnitNote
    },
    primary_metric: 'dissolution_%',
    tablet_results: tabletResults,
    dissolution_profile: meanByTime,
    statistics: { mean_by_timepoint: meanByTime },
    usp_method: { apparatus_type: apparatus, apparatus_description: 'USP Apparatus ' + apparatus, medium_composition: p.medium_composition || 'SGF pH 1.2', medium_volume_ml: vol, rpm: rpm, temperature_C: T },
    acceptance_criteria: { q_value_pct: qVal, q_time_h: qTime, release_at_q_time_pct: qRelease, passes_q_test: !!passesQ, criterion: 'USP <711>: mean release at Q-time must be >= Q' }
  };
}`;

// 备份 + 替换 + 语法校验
const bak = SRC + '.bak-usp-' + Date.now();
fs.writeFileSync(bak, s);
const out = s.slice(0, start) + newFn + s.slice(end);
fs.writeFileSync(SRC, out);

const { execSync } = require('child_process');
try {
  execSync(`node --check "${SRC}"`, { stdio: 'pipe' });
  console.log('✓ 替换完成，语法校验通过');
  console.log(`  原函数 ${oldFn.length} 字符 -> 新函数 ${newFn.length} 字符`);
  console.log(`  备份: ${path.basename(bak)}`);
} catch (e) {
  fs.writeFileSync(SRC, s);
  console.error('✗ 语法校验失败，已回滚');
  console.error(String(e.stderr || e.message).slice(0, 800));
  process.exit(1);
}
