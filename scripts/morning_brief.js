// SwarmLabs 晨报生成器 —— 把隔夜自动化结果收敛成"可讨论清单"
// 运行: node scripts/morning_brief.js
// 产出: reports/morning-brief-YYYY-MM-DD.md (+ .json)
// 用途: 每天早上根据晨报, 与我们逐条讨论哪些需要改进/推广/邀约
const fs = require('fs');
const os = require('os');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const LOG_DIR = path.join(ROOT, 'reports', 'daily-ops-log');
const BRIEF_DIR = path.join(ROOT, 'reports', 'briefs');
const now = new Date();
const today = now.toISOString().slice(0, 10);
const DateNow = () => new Date().toISOString();

function readJson(p, fb) {
  try { if (fs.existsSync(p)) return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) {}
  return fb;
}
function latest(files, dir) {
  return files.map(f => ({ f, t: fs.statSync(path.join(dir, f)).mtimeMs }))
    .sort((a, b) => b.t - a.t)[0]?.f;
}

// ---- 1. 隔夜编排日志 ----
const opLogs = fs.existsSync(LOG_DIR) ? fs.readdirSync(LOG_DIR).filter(f => f.endsWith('.json')) : [];
const latestOps = opLogs.length ? latest(opLogs, LOG_DIR) : null;
const ops = latestOps ? readJson(path.join(LOG_DIR, latestOps), null) : null;
function readJson(p, fb) { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch(e){ return fb; } }

// ---- 2. 部署验证报告 ----
const dvReport = readJson(path.join(ROOT, 'reports', 'issues', 'backlog.json'), null);
// ---- 3. 决策层/backlog 关键线索 ----
const backlog = readJson(path.join(ROOT, 'command-center', 'backlog', 'backlog.json'), null);
const keyDur = backlog ? (backlog.summary || {}) : null;

// ---- 汇总隔夜结果 ----
const opsSummary = ops ? {
  timestamp: ops.timestamp,
  deploy: ops.deploy,
  results: ops.results,
  overall: ops.overall,
  failed: ops.failed || [],
} : { status: '未记录(隔夜未运行?)' };

// 性能基线(取最新)
const perfFiles = fs.existsSync(path.join(ROOT,'reports')) ? fs.readdirSync(path.join(ROOT,'reports')).filter(f=>f.startsWith('perf-baseline')) : [];
let perfInfo = null;
if (perfFiles.length) {
  const p = readJson(path.join(ROOT,'reports', perfFiles.sort((a,b)=>fs.statSync(path.join(ROOT,'reports',b)).mtimeMs-fs.statSync(path.join(ROOT,'reports',a)).mtimeMs)[0]), null);
  if (p) perfInfo = { avg: p.overall_avg_ms, dev: p.deviation_pct, status: p.deviation_status, ts: p.timestamp };
}

// ---- 晨报 ----
const lines = [];
lines.push(`# SwarmLabs 晨报 · ${today}`);
lines.push(`> 生成: ${DateNow()}  |  供早晨讨论「改进/推广/邀约」使用`);
lines.push('');

lines.push('## 一、隔夜自动化概览');
lines.push('```json');
lines.push(JSON.stringify(opsSummary, null, 2));
lines.push('```');
lines.push('');

lines.push('## 二、生产健康');
if (perfInfo) {
  lines.push(`- 平均响应: ${perfInfo.avg}ms (偏差 ${perfInfo.dev}%) 状态${perfInfo.status==='OK'?'✅':'⚠️'}`);
} else {
  lines.push('- 尚无性能基线');
}
lines.push('');

lines.push('## 三、待讨论(改进点)');
if (ops && ops.failed && ops.failed.length) {
  lines.push('- 隔夜失败项: ' + ops.failed.join('、'));
  lines.push('  → 建议优先处理以上自动化未通过环节');
} else {
  lines.push('- 隔夜自动化全部通过，无异常 ✅');
}
lines.push('');

lines.push('## 四、推广动作建议');
lines.push('- 产品已稳定，可进入推广内容触达阶段(引流量/收集意向)');
if (keyDur) {
  if (keyDur.total_registered) lines.push(`- 当前注册用户: ${keyDur.total_registered} · 活跃: ${keyDur.active_users}`);
  if (keyDur.paid_conversion_rate_pct !== undefined) lines.push(`- 付费转化率: ${keyDur.paid_conversion_rate_pct}% (GMV 关键指标)`);
  if (keyDur.new_user_activation_rate_pct !== undefined) lines.push(`- 新用户激活率: ${keyDur.new_user_activation_rate_pct}%`);
}
lines.push('');

lines.push('## 五、邀约专业用户试验');
lines.push('- 对已完成度高的仿真模块，可邀约特定专业人员试用并收集反馈');
if (keyDur && keyDur.user_personas) {
  lines.push('- 目标画像分布: ' + JSON.stringify(keyDur.user_personas));
  lines.push('  → 优先邀约画像占比高的专业研究者(pro/team)参与试用');
}
lines.push('');

lines.push('---');
lines.push('生成脚本: scripts/morning_brief.js  (可每日 05:30 自动运行)');

const md = lines.join('\n');
if (!fs.existsSync(BRIEF_DIR)) fs.mkdirSync(BRIEF_DIR, { recursive: true });
fs.writeFileSync(path.join(BRIEF_DIR, `morning-brief-${today}.md`), md);
fs.writeFileSync(path.join(BRIEF_DIR, `morning-brief-${today}.json`),
  JSON.stringify({ generated_at: DateNow(), ops: opsSummary, perf: perfInfo }, null, 2));
console.log('晨报已生成: reports/morning-briefs/' + today + '.md');