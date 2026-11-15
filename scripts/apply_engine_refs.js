#!/usr/bin/env node
/**
 * apply_engine_refs.js — 将 CrossRef 重建的真实文献写回后端 refMap。
 * 逐条精确替换，保留未能重建的原条目(并标注待处理)，写入前自动备份。
 */
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, '..', 'frontend_fixed', 'functions', 'api', '[[path]].js');
const REBUILT = path.join(__dirname, '..', 'data', 'engine-refs-rebuilt.json');

const data = JSON.parse(fs.readFileSync(REBUILT, 'utf8'));
let src = fs.readFileSync(SRC, 'utf8');

// 备份
const bak = SRC + '.bak-refs-' + Date.now();
fs.writeFileSync(bak, src);
console.log('已备份原文件 ->', path.basename(bak));

let replaced = 0, notFound = 0;
for (const r of data.refs) {
  // 匹配该引擎的整条记录
  const re = new RegExp(`(\\b${r.engine}:\\s*\\{doi:')[^']+(',\\s*title:')[^']*(',\\s*journal:')[^']*(',\\s*year:)\\d+(,\\s*citations:)\\d+(\\})`);
  if (!re.test(src)) { notFound++; continue; }
  src = src.replace(re, `$1${r.doi}$2${r.title.replace(/'/g, '')}$3${r.journal.replace(/'/g, '')}$4${r.year}$5${r.citations}$6`);
  replaced++;
}

fs.writeFileSync(SRC, src);
console.log(`替换完成: ${replaced} 条真实文献已写入, ${notFound} 条未匹配`);

// 校验语法
const { execSync } = require('child_process');
try {
  execSync(`node --check "${SRC}"`, { stdio: 'pipe' });
  console.log('✓ 语法检查通过');
} catch (e) {
  console.error('✗ 语法错误! 正在回滚…');
  fs.copyFileSync(bak, SRC);
  console.error(String(e.stderr || e.message).slice(0, 500));
  process.exit(1);
}
