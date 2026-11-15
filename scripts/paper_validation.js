// 论文基准数据 → 生产平台真实实验验证
// 目标: 用公开文献的"证书/基准数据"调用 swarmlabs.tools 各域引擎,
// 对比平台输出与文献实测/验收区间, 证明平台能跑真实实验并覆盖多专业场景。
// 用法: BASE_URL=https://swarmlabs.tools/api node scripts/paper_validation.js

const BASE_URL = (process.env.BASE_URL || 'https://swarmlabs.tools/api').replace(/\/$/, '');

// 文献基准数据（来源见每项 source）
const CASES = [
  {
    domain: 'Pharma',
    engine: 'release_fit',
    label: 'HPMC K100M 缓释骨架 (MDPI 2022, Tab-K100M)',
    source: 'Polymers 2022, 14, 3406: Korsmeyer-Peppas n=0.481, k=3.839 min^-n, T50≈209min',
    expect: '拟合 n∈[0.45,0.55], 释放机制=Anomalous非Fickian (0.45-0.89)',
    params: {
      time_points: [30, 60, 120, 210, 300, 485],          // 分钟
      release_pct: [19.7, 27.5, 38.4, 50.3, 59.7, 75.2],   // 由文献 k,n 推导的累积释放%
      drug_load_mg: 100
    }
  },
  {
    domain: 'Pharma',
    engine: 'release_fit',
    label: '异烟肼亲水骨架片 (PMC2628280, H2)',
    source: 'Indian J Pharm Sci: n≈0.60, t50%≈1.25h',
    expect: '拟合 n∈[0.55,0.65], 机制=Anomalous',
    params: {
      time_points: [0.5, 1, 2, 4, 8],   // 小时
      release_pct: [18.0, 27.3, 41.3, 62.7, 95.0],
      drug_load_mg: 100
    }
  },
  {
    domain: 'Energy',
    engine: 'battery_li_ion',
    label: 'NMC811 21700 循环衰减 (J. Power Sources 2024, 234185)',
    source: 'LG M50T @25°C,1C: ~1000 cycles 容量保持≈80-85%',
    expect: '容量保持率随循环数单调下降; 1000 cycles ≈ 80-90%',
    params: { temperature_C: 25, cycle_count: 1000, current_density: 1.0, voltage_V: 3.7, discharge_rate: 1.0, time_h: 1000 }
  },
  {
    domain: 'Energy',
    engine: 'battery_li_ion',
    label: '同单元 500 / 2000 cycles 对照 (验证单调性)',
    source: '同上数据集: 衰减随循环数加速',
    expect: '2000 cycles 保持率 < 1000 < 500',
    params: { temperature_C: 25, cycle_count: 500, current_density: 1.0, voltage_V: 3.7, discharge_rate: 1.0, time_h: 500 }
  },
  {
    domain: 'CFD',
    engine: 'cfd_cylinder',
    label: '圆柱绕流 Re=100 (经典基准)',
    source: '文献: Re=100 时阻力系数 Cd≈1.2-1.35',
    expect: '输出含阻力系数/Cd, 量级≈1.0-1.5',
    params: { reynolds_number: 100, time_h: 50, inlet_velocity: 1.0, viscosity: 0.01, mesh_size: 0.01 }
  },
  {
    domain: 'HeatTransfer',
    engine: 'heat_chip',
    label: '芯片稳态导热 (1D 解析对照)',
    source: '稳态1D导热: 芯片温度介于环境温度与热源之间, 随热流升高',
    expect: '温度场数值合理(>ambient, 随heat_flux升高)',
    params: { temperature_C: 100, time_h: 60, thermal_conductivity: 50, heat_flux: 1000, ambient_temp_C: 25 }
  },
  {
    domain: 'Structural',
    engine: 'struct_beam',
    label: '悬臂梁挠度 (材料力学解析对照)',
    source: 'δ=FL³/(3EI); 钢 E≈210GPa, 正位移、随载荷增大',
    expect: '位移为正且随load_N增大; 量级合理(mm级)',
    params: { youngs_modulus: 210000, load_N: 1000, time_h: 1, poisson_ratio: 0.3, density: 7850, length_m: 1.0 }
  }
];

async function runCase(c) {
  const url = `${BASE_URL}/v2/run/${c.engine}`;
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 25000);
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-cache' },
      body: JSON.stringify(c.params),
      signal: ctrl.signal
    });
    const json = await res.json();
    clearTimeout(t);
    return { ok: res.ok, status: res.status, json };
  } catch (e) {
    clearTimeout(t);
    return { ok: false, status: 0, json: { error: String(e && e.message || e) } };
  }
}

function summarize(c, r) {
  const j = r.json || {};
  const res = j.result !== undefined ? j.result : j;
  // 抽取常见关键字段
  const keys = ['capacity_retention', 'drag_coefficient', 'Cd', 'velocity_accuracy',
    'temperature_field', 'displacement', 'dissolution', 'cumulative_release_pct',
    'kinetic_models', 'release_mechanism', 'best_r2', 'n_fickian_threshold'];
  const picked = {};
  for (const k of keys) if (j[k] !== undefined) picked[k] = j[k];
  if (j.kinetic_models) picked.release_mechanism = j.kinetic_models.release_mechanism;
  if (j.kinetic_models && j.kinetic_models.models) {
    const kp = j.kinetic_models.models.find(m => m.name === 'Korsmeyer-Peppas');
    if (kp) picked.kp_n = kp.params && kp.params.n;
    picked.best_fit = j.kinetic_models.best_fit;
    picked.best_r2 = j.kinetic_models.best_r2;
  }
  return { domain: c.domain, engine: c.engine, label: c.label, source: c.source,
    expect: c.expect, http: r.status, ok: r.ok, result: res, picked };
}

(async () => {
  const out = [];
  for (const c of CASES) {
    const r = await runCase(c);
    const s = summarize(c, r);
    out.push(s);
    console.log(`\n=== [${s.domain}] ${s.label} ===`);
    console.log(`HTTP ${s.http} ok=${s.ok}`);
    console.log('文献基准:', s.source);
    console.log('预期:', s.expect);
    console.log('平台输出关键字段:', JSON.stringify(s.picked, null, 2));
    if (s.result && typeof s.result === 'object') {
      console.log('完整 result(截断):', JSON.stringify(s.result).slice(0, 600));
    } else {
      console.log('result:', s.result);
    }
  }
  const fs = require('fs');
  const path = require('path');
  const file = path.join(__dirname, '..', 'reports', 'paper-validation-2026-08-04.json');
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify({ base_url: BASE_URL, generated: new Date().toISOString(), cases: out }, null, 2));
  console.log(`\n[done] wrote ${file}`);
})();
