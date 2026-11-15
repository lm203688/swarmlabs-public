// SwarmLabs T2 — 基线回归构建器
// 用途: 以当前模型验证结果为回归基线写入 data/model-validation-baseline.json，
//       使后续每日 validate_models 能真正对比历史基线、检测回归（而非"自比对"假阳性）。
// 运行: node scripts/build_baseline.js
// 调度: 建议每日 23:30（daily_ops 之前）由 nightly-ops 调用
const { execFileSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const ROOT = path.resolve(__dirname, '..');
const BASELINE = path.join(ROOT, 'data', 'model-validation-baseline.json');

function run() {
  console.log(`[T2] 重建模型回归基线 @ ${new Date().toISOString()}`);
  try {
    execFileSync(process.execPath, ['scripts/validate_models.js', '--write-baseline'], {
      cwd: ROOT, stdio: 'inherit'
    });
  } catch (e) {
    console.error('[T2] validate_models 执行失败，基线未更新:', e.message);
    process.exit(1);
  }
  if (fs.existsSync(BASELINE)) {
    const size = (fs.statSync(BASELINE).size / 1024).toFixed(1);
    console.log(`[T2] ✓ 基线已写入: ${BASELINE} (${size} KB)`);
  } else {
    console.error('[T2] ✗ 基线文件未生成');
    process.exit(1);
  }
}

run();
