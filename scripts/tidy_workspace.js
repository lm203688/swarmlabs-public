// SwarmLabs 工作区整理器（安全模式：只移动，不删除）
// 运行: node scripts/tidy_workspace.js
// 作用:
//   1) reports/ 中与 command-center/ 完全相同(MD5)的镜像文件 -> _cleanup_pending/reports-mirror/
//   2) 7月旧报告的静态资产目录 -> archive/2026-07/
//   3) 清理产生的空目录
// 保留: reports/daily-ops-log, reports/briefs, reports/promo, reports/perf-baseline.json (脚本活跃写入路径)
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const ROOT = path.resolve(__dirname, '..');
process.chdir(ROOT);

const md5 = p => crypto.createHash('md5').update(fs.readFileSync(p)).digest('hex');
const KEEP_DIRS = [
  path.join('reports', 'daily-ops-log'),
  path.join('reports', 'briefs'),
  path.join('reports', 'promo'),
];
const KEEP_FILES = [path.join('reports', 'perf-baseline.json')];

function walk(dir, fn) {
  if (!fs.existsSync(dir)) return;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, fn);
    else fn(p);
  }
}

// 1) 建立 command-center 指纹表（权威副本所在地）
const fingerprint = {};
walk('command-center', p => { try { fingerprint[md5(p)] = p; } catch {} });

// 2) 隔离 reports / content 中的重复镜像（command-center 为唯一权威副本）
let moved = 0, bytes = 0;
for (const src of ['reports', 'content']) {
  const DST = path.join('_cleanup_pending', src + '-mirror');
  walk(src, p => {
    if (KEEP_DIRS.some(k => p.startsWith(k))) return;
    if (KEEP_FILES.includes(p)) return;
    let h;
    try { h = md5(p); } catch { return; }
    if (!fingerprint[h]) return;
    const target = path.join(DST, path.relative(src, p));
    fs.mkdirSync(path.dirname(target), { recursive: true });
    bytes += fs.statSync(p).size;
    fs.renameSync(p, target);
    moved++;
  });
}
console.log(`[1] 隔离重复镜像: ${moved} 个文件, ${(bytes / 1048576).toFixed(2)} MB -> _cleanup_pending/`);

// 3) 归档 7 月旧报告的静态资产（逐文件移动，规避 Windows 目录锁）
const ARCHIVE = path.join('archive', '2026-07');
let arch = 0, archBytes = 0;
for (const legacy of ['health-daily-2026-07-29', 'health-daily-2026-07-30']) {
  const src = path.join('reports', legacy);
  if (!fs.existsSync(src)) continue;
  walk(src, p => {
    const target = path.join(ARCHIVE, path.relative('reports', p));
    fs.mkdirSync(path.dirname(target), { recursive: true });
    archBytes += fs.statSync(p).size;
    fs.renameSync(p, target);
    arch++;
  });
}
console.log(`[2] 归档 7 月旧资产: ${arch} 个文件, ${(archBytes / 1048576).toFixed(2)} MB -> ${ARCHIVE}`);

// 4) 递归删除空目录（仅限本次整理涉及的范围）
function pruneEmpty(dir) {
  if (!fs.existsSync(dir)) return;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.isDirectory()) pruneEmpty(path.join(dir, e.name));
  }
  try {
    if (fs.readdirSync(dir).length === 0) { fs.rmdirSync(dir); console.log('    移除空目录: ' + dir); }
  } catch {}
}
console.log('[3] 清理空目录:');
['reports', 'issues'].forEach(pruneEmpty);

console.log('\n整理完成。被隔离的文件仍保留在 _cleanup_pending/，确认无误后可整体删除。');
