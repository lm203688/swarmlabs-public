/**
 * 实验工作流回归测试（jsdom，无需真实浏览器）
 *
 * 覆盖 2026-09-09 修复的三处问题：
 *   A) 文献检索跨领域：主题→领域自动识别；检索请求必须带上识别出的 domain
 *   B) 微生物实验无法配置：必须有培养条件 + 器皿参数，且能自动判领域
 *   C) 参数扫描：下拉可选、选中态可见；后端两种返回结构都能解析；空结果不静默跳转
 *
 * 运行：
 *   NODE_PATH=<workspace>/node_modules node scripts/verify_lab_workflow.js
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const HTML = fs.readFileSync(
  path.join(__dirname, '..', 'frontend_fixed', 'lab.html'), 'utf8');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;
function check(name, cond, extra) {
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${extra !== undefined ? '  → ' + extra : ''}`);
  if (!cond) failures++;
}

// 记录 fetch 请求体，便于断言"domain 是否真的发出去了"
function makeWindow(fetchImpl) {
  const captured = [];
  const dom = new JSDOM(HTML, {
    runScripts: 'dangerously',
    url: 'https://swarmlabs.tools/lab',
    beforeParse(window) {
      window.__captured = captured;
      window.__navTo = null;
      window.fetch = function (url, opts) {
        captured.push({ url, body: opts && opts.body });
        return fetchImpl(url, opts);
      };
      window.requestAnimationFrame = (cb) => setTimeout(cb, 0);
      window.scrollTo = () => {};
      // jsdom 未实现元素级 scrollTo / scrollIntoView，补桩避免测试被无关报错打断
      window.Element.prototype.scrollTo = function () {};
      window.Element.prototype.scrollIntoView = function () {};
      // jsdom 不加载 CDN 上的 echarts，补一个最小桩，避免渲染图表时报错干扰断言
      window.echarts = {
        init: function () { return { setOption() {}, resize() {}, dispose() {}, on() {} }; },
        getInstanceByDom: function () { return null; },
      };
      // lab.html 初始化会用到这些；缺失时静默降级即可
      window.matchMedia = window.matchMedia || (() => ({ matches: false, addListener() {}, removeListener() {} }));
    },
  });
  return dom.window;
}

const okJson = (obj) => Promise.resolve({ ok: true, status: 200, json: async () => obj });

(async function main() {
  // ── A1. 领域自动识别 ────────────────────────────────────────────
  console.log('\n[A1] 领域自动识别 detectDomain()');
  const w = makeWindow(() => okJson({ papers: [], status: 'success' }));
  await sleep(150);
  const cases = [
    ['E. coli fed-batch fermentation optimization', 'Microbiology'],
    ['微生物发酵培养基优化', 'Microbiology'],
    ['Suzuki coupling optimization', 'Chemistry'],
    ['lithium battery capacity retention', 'Energy'],
  ];
  for (const [q, want] of cases) {
    const d = w.detectDomain(q);
    check(`${q.slice(0, 40).padEnd(42)} → ${want}`,
      !!d && d.domain === want, d ? d.domain : 'null');
  }
  check('空主题不误判', w.detectDomain('') === null);

  // ── A2. 检索请求带上识别出的领域 ────────────────────────────────
  console.log('\n[A2] 检索请求携带 domain（修复前恒为空，后端领域过滤形同虚设）');
  const w2 = makeWindow(() => okJson({
    papers: [], status: 'success', domain_detected: 'Microbiology', domain_source: 'keyword_inferred',
  }));
  await sleep(150);
  w2.document.getElementById('litTopic').value = 'E. coli fed-batch fermentation optimization';
  w2.updateDomainHint((w2.detectDomain('E. coli fed-batch fermentation optimization') || {}).domain, 'keyword');
  w2.searchLiterature();
  await sleep(200);
  const litReq = w2.__captured.find((c) => String(c.url).includes('/literature/search'));
  check('已发出文献检索请求', !!litReq);
  const body = litReq ? JSON.parse(litReq.body) : {};
  check('domain 字段非空且为 Microbiology', body.domain === 'Microbiology', 'domain=' + body.domain);

  const hint = w2.document.getElementById('domainHint');
  check('页面显示识别到的领域', hint && hint.innerHTML.indexOf('Microbiology') >= 0);
  check('后端 domain_detected 覆盖前端猜测（hint 已刷新）',
    hint && hint.innerHTML.indexOf('by literature search') >= 0);

  // ── B. 微生物培养条件 + 器皿 ────────────────────────────────────
  console.log('\n[B] 微生物引擎参数（培养条件 + 器皿）');
  const wp = w.getParams('microbial_growth');
  const keys = wp.map((x) => x.k);
  const need = ['vessel_type', 'medium_type', 'temperature_C', 'pH', 'time_h',
    'substrate_g_L', 'inoculum_pct', 'dissolved_oxygen_pct', 'agitation_rpm'];
  for (const k of need) check(`  含参数 ${k}`, keys.indexOf(k) >= 0);
  const vessel = wp.find((x) => x.k === 'vessel_type');
  const medium = wp.find((x) => x.k === 'medium_type');
  check('器皿为下拉选择', !!vessel && vessel.type === 'select',
    vessel ? JSON.stringify(vessel.options) : 'missing');
  check('培养基为下拉选择', !!medium && medium.type === 'select');
  check('微生物引擎归类为 Microbiology', w.getEngineCategory('microbial_growth') === 'Microbiology');
  check('分类标签含 Microbiology',
    (function () { w.renderEngines(); return w.document.getElementById('catTags').innerHTML.indexOf('Microbiology') >= 0; })());

  // ── C1. 扫描下拉与选中态 ────────────────────────────────────────
  console.log('\n[C1] 参数扫描：下拉填充 + 选中态高亮');
  const w3 = makeWindow(() => okJson({}));
  await sleep(150);
  w3.selectEngine('microbial_growth');
  await sleep(50);
  const sel = w3.document.getElementById('sweepParamSelect');
  check('扫描下拉已填充', !!sel && sel.options.length > 0, 'n=' + (sel ? sel.options.length : 0));
  const rows = w3.document.querySelectorAll('#sweepBody tr');
  check('扫描表每行带 data-k（供高亮）',
    rows.length > 0 && Array.from(rows).every((r) => r.getAttribute('data-k')));
  check('默认高亮首个参数',
    Array.from(rows).filter((r) => r.style.background).length === 1);
  // 切换下拉 → 高亮跟随
  if (sel && sel.options.length > 2) {
    sel.value = '2';
    w3.highlightSweepRow('2');
    const hl = Array.from(rows).filter((r) => r.style.background);
    const p = w3.getParams('microbial_growth').filter((x) => x.type !== 'select');
    check('切换后高亮跟随所选参数', hl.length === 1 && hl[0].getAttribute('data-k') === p[2].k,
      hl[0] && hl[0].getAttribute('data-k'));
  }

  // ── C2. 扫描结果解析（新版 sweep.results）───────────────────────
  console.log('\n[C2] 扫描结果解析：新版 {sweep:{results}} ');
  let sweepBody = null;
  const w4 = makeWindow((url, opts) => {
    if (String(url).includes('/v2/sweep/')) { sweepBody = JSON.parse(opts.body); return okJson({
      engine: 'microbial_growth', sweep_param: 'temperature_C', points: 3,
      sweep: { n_combinations: 3, results: [
        { index: 0, params: { temperature_C: 20 }, result: 1.1, uncertainty: 5 },
        { index: 1, params: { temperature_C: 30 }, result: 2.2, uncertainty: 5 },
        { index: 2, params: { temperature_C: 37 }, result: 3.3, uncertainty: 5 },
      ] },
      series: [], status: 'success' });
    }
    return okJson({});
  });
  await sleep(150);
  w4.selectEngine('microbial_growth');
  await sleep(50);
  // 设定：扫 temperature_C，20→37，3 步
  w4.document.getElementById('sweepParamSelect').value = '0';
  const pnum = w4.getParams('microbial_growth').filter((x) => x.type !== 'select');
  const tKey = pnum.findIndex((x) => x.k === 'temperature_C');
  w4.document.getElementById('sweepParamSelect').value = String(tKey);
  w4.document.getElementById('s_temperature_C_min').value = '20';
  w4.document.getElementById('s_temperature_C_max').value = '37';
  w4.document.getElementById('s_temperature_C_steps').value = '3';
  await w4.runSweep();
  check('请求体含 param_combos', !!sweepBody && Array.isArray(sweepBody.param_combos),
    sweepBody ? 'n=' + sweepBody.param_combos.length : 'null');
  check('扫描点数量与步数一致', sweepBody && sweepBody.param_combos.length === 3);
  check('结果已写入 experimentResults', (w4.experimentResults || []).length === 3,
    'n=' + (w4.experimentResults || []).length);
  check('结果保留用户所选参数值',
    (w4.experimentResults || []).map((r) => r.sweepVal).join(',') === '20,30,37',
    JSON.stringify((w4.experimentResults || []).map((r) => r.sweepVal)));

  // ── C3. 兼容旧版顶层 series ────────────────────────────────────
  console.log('\n[C3] 扫描结果解析：旧版顶层 {series}');
  const w5 = makeWindow((url) => {
    if (String(url).includes('/v2/sweep/')) return okJson({
      engine: 'microbial_growth', sweep_param: 'temperature_C', points: 2,
      series: [
        { param: 'temperature_C', value: 20, result: 1.1, uncertainty: 5 },
        { param: 'temperature_C', value: 37, result: 3.3, uncertainty: 5 },
      ], status: 'success' });
    return okJson({});
  });
  await sleep(150);
  w5.selectEngine('microbial_growth');
  await sleep(50);
  const pnum5 = w5.getParams('microbial_growth').filter((x) => x.type !== 'select');
  w5.document.getElementById('sweepParamSelect').value = String(pnum5.findIndex((x) => x.k === 'temperature_C'));
  w5.document.getElementById('s_temperature_C_min').value = '20';
  w5.document.getElementById('s_temperature_C_max').value = '37';
  w5.document.getElementById('s_temperature_C_steps').value = '2';
  await w5.runSweep();
  check('旧版结构也能解析出结果', (w5.experimentResults || []).length === 2,
    'n=' + (w5.experimentResults || []).length);

  // ── C4. 空结果不静默跳转 ───────────────────────────────────────
  console.log('\n[C4] 空结果：应提示而不是静默跳到 Step5');
  const w6 = makeWindow((url) => {
    if (String(url).includes('/v2/sweep/')) return okJson({ engine: 'x', sweep: {}, series: [], status: 'success' });
    return okJson({});
  });
  await sleep(150);
  w6.selectEngine('microbial_growth');
  await sleep(50);
  const pnum6 = w6.getParams('microbial_growth').filter((x) => x.type !== 'select');
  w6.document.getElementById('sweepParamSelect').value = String(pnum6.findIndex((x) => x.k === 'temperature_C'));
  w6.document.getElementById('s_temperature_C_min').value = '20';
  w6.document.getElementById('s_temperature_C_max').value = '37';
  w6.document.getElementById('s_temperature_C_steps').value = '2';
  const before4 = (w6.experimentResults || []).length;
  await w6.runSweep();
  check('空结果时不跳转（仍停留在 Step4）',
    w6.document.getElementById('step4').style.display !== 'none',
    'step4.display=' + w6.document.getElementById('step4').style.display);
  check('空结果不覆盖已有实验结果',
    (w6.experimentResults || []).length === before4,
    'before=' + before4 + ' after=' + (w6.experimentResults || []).length);

  console.log('\n========================================');
  console.log(`  ${failures === 0 ? 'ALL PASS' : failures + ' FAILED'}`);
  console.log('========================================');
  process.exit(failures === 0 ? 0 : 1);
})();
