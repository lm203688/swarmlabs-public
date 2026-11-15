// SwarmLabs T3 — 自愈修复器（每日 04:00）
// 作用: 补齐"检测 → 修复"断点。
//   1) 扫描 reports/issues/backlog.json 中的 open / P0 工单
//   2) 对已知签名匹配安全修复器(REMEDIATORS)，自动应用并复验
//   3) 将"已修复待部署"(fixed_pending_deploy) 工单与本次新修复工单推入 deploy-queue.json，
//      交给 T4 部署守门人完成"测试→部署→复验→关单"闭环
// 设计原则: 任何单步失败都被隔离，绝不影响其余工单；不部署、不推送、不删除。
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const ISSUES_DIR = path.join(ROOT, 'reports', 'issues');
const BACKLOG = path.join(ISSUES_DIR, 'backlog.json');
const QUEUE = path.join(ISSUES_DIR, 'deploy-queue.json');
const REPORT = path.join(ISSUES_DIR, `self-heal-${new Date().toISOString().slice(0, 10)}.json`);

function readJson(p, fb) { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) { return fb; } }
function writeJson(p, o) { fs.mkdirSync(path.dirname(p), { recursive: true }); fs.writeFileSync(p, JSON.stringify(o, null, 2)); }
function ts() { return new Date().toISOString(); }

// ===== 已知安全修复器（按 gap_id / 关键词匹配） =====
// 每个修复器返回 true 表示已应用修复（需经复验），false 表示无法自动处理。
const REMEDIATORS = {
  // 示例: 基线缺失类问题 -> 重建基线
  'baseline-missing': {
    match: (it) => /baseline|基线/.test(it.title || it.error || ''),
    apply: () => { execFileSync(process.execPath, ['scripts/build_baseline.js'], { cwd: ROOT, stdio: 'inherit' }); return true; }
  },
  // 示例: 日志清理失效类 -> 触发 daily_ops 重跑
  'log-cleanup': {
    match: (it) => /log|日志|MAX_AGE/.test(it.title || it.error || ''),
    apply: () => { execFileSync(process.execPath, ['scripts/daily_ops.js'], { cwd: ROOT, stdio: 'inherit' }); return true; }
  }
};

function tryRemediate(item) {
  for (const [key, r] of Object.entries(REMEDIATORS)) {
    try {
      if (r.match(item)) {
        console.log(`  [修复] ${item.id || item.gap_id} 命中修复器 ${key}，应用修复...`);
        r.apply();
        // 复验: 跑一次模型验证，确保未引入回归
        execFileSync(process.execPath, ['scripts/validate_models.js'], { cwd: ROOT, stdio: 'ignore' });
        return key;
      }
    } catch (e) {
      console.error(`  [修复] ${item.id || item.gap_id} 修复器 ${key} 执行失败: ${e.message}`);
    }
  }
  return null;
}

function main() {
  console.log(`\n=== SwarmLabs T3 自愈修复器 @ ${ts()} ===`);
  const backlog = readJson(BACKLOG, { issues: [] });
  const issues = Array.isArray(backlog.issues) ? backlog.issues : [];
  console.log(`工单池总数: ${issues.length}`);

  const queue = [];
  const healed = [];
  const escalated = [];
  const open = issues.filter(i => (i.status || 'open') === 'open' || i.status === 'P0' || i.severity === 'critical');

  for (const it of open) {
    const rem = tryRemediate(it);
    if (rem) {
      it.status = 'fixed_pending_deploy';
      it.healed_by = rem;
      it.healed_at = ts();
      healed.push({ id: it.id || it.gap_id, by: rem });
    } else {
      escalated.push({ id: it.id || it.gap_id, title: it.title, reason: '无匹配自动修复器' });
    }
  }

  // 收集所有待部署工单（含此前已 fixed_pending_deploy 的存量积压 -> 核心断点）
  for (const it of issues) {
    if (it.status === 'fixed_pending_deploy') {
      queue.push({ id: it.id || it.gap_id, title: it.title, healed_by: it.healed_by || 'prior', healed_at: it.healed_at || null });
    }
  }

  writeJson(QUEUE, { generated_at: ts(), count: queue.length, items: queue });
  writeJson(REPORT, {
    generated_at: ts(),
    open_scanned: open.length,
    auto_healed: healed,
    escalated_to_human: escalated,
    deploy_queue_size: queue.length,
    note: 'deploy-queue.json 已生成，交由 T4 部署守门人处理'
  });

  console.log(`自动修复: ${healed.length} | 升级人工: ${escalated.length} | 待部署队列: ${queue.length}`);
  if (queue.length) console.log(`→ 已写入 ${QUEUE}，请运行 node scripts/deploy_gate.js 完成闭环`);
}

main();
