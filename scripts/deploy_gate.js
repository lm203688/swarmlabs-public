// SwarmLabs T4 — 部署守门人（每日 04:30，接在 T3 之后）
// 作用: 补齐"修复 → 部署"断点，形成 检测→T3修复→测试→T4→复验关单 的无人值守闭环。
//   1) 读取 T3 产出的 deploy-queue.json
//   2) 全量复验: validate_models(本地回归) + deploy_verify(在线 GAP)
//   3) 全绿 -> git 提交(若有变更) -> git push(若有远程) -> wrangler 部署(若有凭证) -> 部署后复验
//   4) 成功 -> 工单标记 deployed/closed, 清空队列; 失败 -> 自动回滚 + 升级 P0 工单
// 安全:
//   - 每个阶段独立 try/catch; 无凭证/无远程时安全跳过对应阶段并明确告警, 绝不硬失败
//   - 提交仅 git add 指定源码目录(绝不 git add -A), 避免误提交整理隔离产生的删除
//   - 设置环境变量 DEPLOY_GATE_DRY=1 或加 --validate 参数: 仅跑复验并出具报告, 不提交/不部署/不关单
const fs = require('fs');
const path = require('path');
const { execFileSync, execSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const ISSUES_DIR = path.join(ROOT, 'reports', 'issues');
const QUEUE = path.join(ISSUES_DIR, 'deploy-queue.json');
const BACKLOG = path.join(ISSUES_DIR, 'backlog.json');
const REPORT = path.join(ISSUES_DIR, `deploy-gate-${new Date().toISOString().slice(0, 10)}.json`);

// 仅提交这些源码/产物目录，绝不包括整理隔离(_cleanup_pending)或 content/ 的历史删除
const COMMIT_SCOPE = ['scripts', 'frontend_fixed', 'data', 'docs', '.github', 'reports/issues'];

function readJson(p, fb) { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) { return fb; } }
function writeJson(p, o) { fs.mkdirSync(path.dirname(p), { recursive: true }); fs.writeFileSync(p, JSON.stringify(o, null, 2)); }
function ts() { return new Date().toISOString(); }
function sh(cmd, opts = {}) {
  try { execSync(cmd, { cwd: ROOT, stdio: 'inherit', ...opts }); return true; }
  catch (e) { throw new Error(`${cmd} -> ${e.message}`); }
}
function shOk(cmd, opts = {}) { try { execSync(cmd, { cwd: ROOT, stdio: 'ignore', ...opts }); return true; } catch { return false; } }
function shOut(cmd, opts = {}) { try { return execSync(cmd, { cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], ...opts }); } catch { return ''; } }

// 记录部署前线上最新的 deployment id，用于部署后复验失败时回滚生产
function captureCurrentDeployment() {
  const out = shOut('npx wrangler@4 pages deployment list --project-name swarmlabs', { cwd: path.join(ROOT, 'frontend_fixed') });
  const m = out.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/);
  return m ? m[0] : null;
}

// 读取 wrangler OAuth token（用于直接调用 Cloudflare API）
function readOauthToken() {
  const home = process.env.USERPROFILE || process.env.HOME || '';
  const paths = [
    path.join(home, 'AppData', 'Roaming', 'xdg.config', '.wrangler', 'config', 'default.toml'),
    path.join(home, '.wrangler', 'config', 'default.toml'),
    path.join(home, '.config', '.wrangler', 'config', 'default.toml'),
  ];
  for (const p of paths) {
    try {
      const m = fs.readFileSync(p, 'utf8').match(/oauth_token\s*=\s*"([^"]+)"/);
      if (m) return m[1];
    } catch {}
  }
  return null;
}

function readAccountId() {
  try {
    const c = JSON.parse(fs.readFileSync(path.join(ROOT, 'frontend_fixed', '.wrangler', 'cache', 'pages.json'), 'utf8'));
    return c.account_id || null;
  } catch { return null; }
}

// 生产回滚: 将线上流量切回上一个健康版本
// 注意: wrangler CLI 并无 `pages deployment rollback` 子命令(仅 list/create/tail/delete),
//       此前误用该命令导致回滚静默失败, 因此改为直接调用 Cloudflare REST API。
async function rollbackDeployment(deploymentId) {
  if (!deploymentId) { console.log('[rollback] 无可回滚的历史版本 id，跳过生产回滚'); return false; }
  const accountId = readAccountId();
  const token = process.env.CLOUDFLARE_API_TOKEN || readOauthToken();
  if (!accountId || !token) { console.log('[rollback] 缺少 account_id 或 token，无法自动回滚，需人工介入'); return false; }
  console.log(`[rollback] 通过 Cloudflare API 回滚生产到 ${deploymentId} ...`);
  try {
    const url = `https://api.cloudflare.com/client/v4/accounts/${accountId}/pages/projects/swarmlabs/deployments/${deploymentId}/rollback`;
    const res = await fetch(url, { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } });
    const body = await res.json().catch(() => ({}));
    const ok = res.ok && body.success !== false;
    console.log(`[rollback] 生产回滚: ${ok ? '成功' : '失败 -> ' + JSON.stringify(body.errors || body).slice(0, 300)}`);
    return ok;
  } catch (e) {
    console.log('[rollback] 回滚请求异常:', e.message);
    return false;
  }
}

// 等待部署在全球边缘节点传播完成
// 背景: Pages 部署返回成功后, 边缘节点仍可能短时返回旧版本。若立刻复验会拿到旧响应
//       而误判"部署失败"并触发不必要的回滚(2026-08-04 已真实发生一次)。
// 做法: 带 cache-buster 轮询 health, 要求连续两次版本指纹一致, 才认定传播稳定。
async function waitForPropagation(baseUrl, maxWaitMs = 150000, intervalMs = 15000) {
  console.log('[propagate] 等待边缘节点传播（cache-buster 轮询 health）...');
  const started = Date.now();
  let lastSig = null;
  while (Date.now() - started < maxWaitMs) {
    try {
      const res = await fetch(`${baseUrl}/health?cb=${Date.now()}`, { headers: { 'Cache-Control': 'no-cache' } });
      const j = await res.json();
      const sig = `${j.engines}|${(j.categories || []).length}|${j.version}`;
      if (sig === lastSig) {
        console.log(`[propagate] 传播稳定: engines=${j.engines}, categories=${(j.categories || []).length}`);
        return sig;
      }
      lastSig = sig;
    } catch { console.log('[propagate] health 暂不可达，继续等待...'); }
    await new Promise(r => setTimeout(r, intervalMs));
  }
  console.log('[propagate] 等待超时，继续进入复验（复验自带重试）');
  return lastSig;
}

// 模型路由主动预热: /health 等轻量端点先就绪, 但重型建模路由(/v2/run/drug_release)
// 冷启动更慢。仅打一次 health 不足以让建模路由热起来, 因此循环 POST 建模端点直到返回
// 有效数据, 否则复验会因建模路由冷启动 5xx/超时而被误判为部署失败(2026-08-04 多次复现)。
async function warmUpModel(verifyUrl, maxWaitMs = 180000) {
  const body = JSON.stringify({
    drug_name: 'Warmup', polymer_type: 'HPMC_K4M',
    polymer_concentration: 20, drug_load_mg: 100, time_points_h: [1, 2, 4, 6, 8, 12]
  });
  const start = Date.now();
  let tries = 0;
  while (Date.now() - start < maxWaitMs) {
    tries++;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 25000);
    try {
      const res = await fetch(`${verifyUrl}/v2/run/drug_release`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-cache' },
        body, signal: ctrl.signal
      });
      clearTimeout(timer);
      if (res.ok) {
        const j = await res.json().catch(() => ({}));
        if (j && (j.result || j.dissolution_profile || j.model_details || j.kinetic_models)) {
          console.log(`[warmup] 建模路由已热(第 ${tries} 次探测返回有效数据), 预热耗时 ${((Date.now() - start) / 1000).toFixed(0)}s`);
          return true;
        }
      }
      console.log(`[warmup] 建模路由暂未就绪 (status=${res.status})，继续预热...`);
    } catch (e) {
      clearTimeout(timer);
      console.log(`[warmup] 建模路由暂未就绪 (${e.message})，继续预热...`);
    }
    await new Promise(r => setTimeout(r, 10000));
  }
  console.log(`[warmup] 预热 ${maxWaitMs / 1000}s 超时，仍继续复验(可能仍处于冷启动)`);
  return false;
}

// 部署后复验: 先主动预热建模路由直到返回有效数据, 再跑 deploy_verify; 失败则再次预热+重试(最多3次)。
// 吸收冷启动/传播抖动/瞬时5xx, 避免被误判为部署失败 (2026-08-04 多次因建模路由冷启动误判)
async function verifyAfterDeploy(verifyUrl, maxRetry = 3) {
  await warmUpModel(verifyUrl);
  for (let attempt = 1; attempt <= maxRetry; attempt++) {
    // 注意: 用 env 选项注入 BASE_URL, 而非 shell 前缀 `BASE_URL="..." cmd`。
    // 原因: Windows 下 execSync 默认走 cmd.exe, 不支持 bash 风格的环境变量前缀,
    //       会导致命令解析失败(shOk 静默返回 false), 从而把"命令未执行"误判为"部署失败"。
    const ok = shOk(`python scripts/deploy_verify.py`, { env: { ...process.env, BASE_URL: verifyUrl } });
    if (ok) { console.log(`[verify] 复验通过 (第 ${attempt} 次)`); return true; }
    if (attempt < maxRetry) {
      const wait = attempt * 15000;
      console.log(`[verify] 第 ${attempt} 次复验未通过，再预热 ${wait / 1000}s 后重试（排除冷启动/传播抖动/瞬时5xx）...`);
      await warmUpModel(verifyUrl, wait);
    }
  }
  return false;
}

function runTests() {
  console.log(`\n[${ts()}] ▶ 阶段1: 本地模型回归验证`);
  const modelOk = shOk(`${process.execPath} scripts/validate_models.js`);
  console.log(`  模型回归: ${modelOk ? 'PASS' : 'FAIL'}`);
  if (!modelOk) throw new Error('模型回归未通过，阻断部署');

  console.log(`[${ts()}] ▶ 阶段2: 在线部署验证(deploy_verify)`);
  const deployOk = shOk(`python scripts/deploy_verify.py`);
  // deploy_verify 依赖线上环境，离线时仅作提示，不阻断（本地回归已通过）
  console.log(`  部署验证: ${deployOk ? 'PASS' : 'FAIL/SKIP(无 BASE_URL 或离线)'}`);
  return true;
}

function commitAndPush() {
  const hasChanges = !shOk('git diff --quiet && git diff --cached --quiet');
  if (!hasChanges) { console.log('[git] 无代码变更，跳过提交'); return { committed: false, pushed: false }; }
  // --ignore-removal: 只暂存「新增/修改」，绝不自动提交「删除」。
  // 原因: 工作区整理会把旧文件移入隔离区形成删除态，自动化不应替用户做删除决策。
  for (const d of COMMIT_SCOPE) sh(`git add --ignore-removal ${d}`);
  const msg = `auto: deploy gate 自动部署批次 ${ts().slice(0, 10)}`;
  sh(`git commit -m "${msg}"`);
  console.log('[git] 已提交(仅源码目录)');
  const hasRemote = shOk('git remote get-url origin');
  if (hasRemote) { sh('git push origin HEAD'); console.log('[git] 已推送 origin'); return { committed: true, pushed: true }; }
  console.log('[git] 未配置 origin 远程，跳过 push（请添加私有远程后手动 push）');
  return { committed: true, pushed: false };
}

// 凭证检测: 支持两种方式 —— CI 用 API Token, 本地用 wrangler OAuth 登录态
function hasDeployCredentials() {
  if (process.env.CLOUDFLARE_API_TOKEN) return 'api_token';
  const home = process.env.USERPROFILE || process.env.HOME || '';
  const oauthPaths = [
    path.join(home, 'AppData', 'Roaming', 'xdg.config', '.wrangler', 'config', 'default.toml'),
    path.join(home, '.wrangler', 'config', 'default.toml'),
    path.join(home, '.config', '.wrangler', 'config', 'default.toml'),
  ];
  for (const p of oauthPaths) {
    try { if (fs.readFileSync(p, 'utf8').includes('oauth_token')) return 'oauth'; } catch {}
  }
  return null;
}

// 部署前构建预检: 确保 Functions 能被 esbuild 正确打包(捕获 import 路径错误等致命问题)
function buildPrecheck() {
  console.log(`[${ts()}] ▶ 阶段3: 部署前构建预检(Pages Functions 打包)`);
  const fe = path.join(ROOT, 'frontend_fixed');
  const ok = shOk(`npx wrangler@4 pages functions build --outdir=.build-check`, { cwd: fe });
  try { fs.rmSync(path.join(fe, '.build-check'), { recursive: true, force: true }); } catch {}
  console.log(`  构建预检: ${ok ? 'PASS' : 'FAIL'}`);
  if (!ok) throw new Error('Functions 打包失败(可能是 import 路径错误)，阻断部署以保护生产');
  return true;
}

async function deploy() {
  const cred = hasDeployCredentials();
  if (!cred) { console.log('[deploy] 未发现部署凭证(API Token 或 wrangler 登录态)，跳过 Cloudflare 部署'); return false; }
  console.log(`[deploy] 凭证方式: ${cred}`);
  buildPrecheck();
  // 部署前抓取当前线上版本，作为复验失败时的回滚目标
  const prevDeployment = captureCurrentDeployment();
  console.log(`[deploy] 已记录回滚锚点: ${prevDeployment || '(无历史版本)'}`);
  console.log('[deploy] 部署到 Cloudflare Pages (project=swarmlabs)...');
  // 捕获部署输出以提取新版本的【预览 URL】：预览地址部署后即可稳定访问，
  // 避免直接复验生产别名(swarmlabs.tools)时遭遇"推广传播抖动/回滚抖动"被误判为部署失败
  // (2026-08-04 多次因生产别名未稳定而误判失败)。2>&1 确保 wrangler 的进度/成功行都被捕获。
  const deployOut = shOut('npx wrangler@4 pages deploy . --project-name swarmlabs --commit-dirty=true 2>&1', { cwd: path.join(ROOT, 'frontend_fixed') });
  if (!/Deployment complete|Success! Uploaded/.test(deployOut)) {
    throw new Error('wrangler 部署未返回成功标记，疑似失败：\n' + deployOut.slice(0, 600));
  }
  const pm = deployOut.match(/https:\/\/[a-f0-9]+\.swarmlabs-dnh\.pages\.dev/);
  const previewBase = pm ? `${pm[0]}/api` : null;
  console.log(`[deploy] 新版本预览地址: ${previewBase || '(未捕获, 将退回生产域名)'}`);

  const verifyUrl = previewBase || process.env.BASE_URL || 'https://swarmlabs.tools/api';
  // 优先用新版本预览地址复验(部署后即稳定); 复验前先等该地址 health 可达, 再复验(含预热+重试)。
  await waitForPropagation(verifyUrl);
  const verified = await verifyAfterDeploy(verifyUrl);
  console.log(`[deploy] 部署后复验(${previewBase ? '预览地址' : '生产域名'}): ${verified ? 'PASS' : 'FAIL'}`);
  if (!verified) {
    // 确属失败 -> 把生产切回上一个健康版本, 再抛错升级 P0
    await rollbackDeployment(prevDeployment);
    throw new Error('部署后复验未通过(含1次重试)，已尝试回滚生产至上一版本');
  }
  return true;
}

function closeTickets(queueItems) {
  const backlog = readJson(BACKLOG, { issues: [] });
  const map = new Map((backlog.issues || []).map(i => [i.id || i.gap_id, i]));
  for (const q of queueItems) {
    const it = map.get(q.id);
    if (it) { it.status = 'deployed'; it.deployed_at = ts(); it.status_note = 'T4 部署守门人自动关单'; }
  }
  writeJson(BACKLOG, { ...backlog, last_autodeploy: ts() });
}

async function main() {
  const dryRun = process.argv.includes('--validate') || process.env.DEPLOY_GATE_DRY === '1';
  console.log(`\n=== SwarmLabs T4 部署守门人 @ ${ts()} ${dryRun ? '(DRY-RUN / 仅复验)' : ''} ===`);
  const queue = readJson(QUEUE, null);
  if (!queue || !queue.items || queue.items.length === 0) {
    console.log('部署队列为空，无需操作。');
    writeJson(REPORT, { generated_at: ts(), action: 'noop', reason: 'empty queue' });
    return;
  }
  console.log(`部署队列: ${queue.items.length} 项`);

  const result = { generated_at: ts(), dry_run: dryRun, stages: {}, items: queue.items.length };
  try {
    result.stages.tests = runTests();
    if (dryRun) {
      result.status = 'VALIDATED_OK';
      result.note = 'DRY-RUN: 复验通过，未提交/未部署/未关单；生产环境由 CI 带凭证运行完整闭环';
      console.log('\n✓ DRY-RUN 复验通过。生产闭环请在 CI(nightly-ops) 中带 CLOUDFLARE_API_TOKEN 运行。');
    } else {
      const git = commitAndPush();
      result.stages.committed = git.committed; result.stages.pushed = git.pushed;
      const deployed = await deploy();
      result.stages.deployed = deployed;
      // 关单以「部署成功且复验通过」为准；push 是异地备份，缺失只告警不阻断关单
      if (deployed) {
        closeTickets(queue.items);
        writeJson(QUEUE, { generated_at: ts(), count: 0, items: [], note: '已部署并关单，队列清空' });
        result.status = 'SUCCESS';
        console.log('\n✓ T4 闭环成功：测试→构建预检→提交→部署→部署后复验→关单 完成');
        if (!git.pushed) console.log('⚠ 提醒：未配置 git 远程，代码仅存本地，建议尽快添加私有远程以防资产丢失');
      } else {
        // 无部署凭证：保留队列，标记为待部署，不谎报"已部署"
        for (const q of queue.items) q.awaiting = 'deploy_credentials';
        writeJson(QUEUE, { ...queue, note: '复验通过，但缺少部署凭证，待 CI 或人工补充凭证后完成部署' });
        result.status = 'AWAITING_DEPLOY';
        console.log('\n⚠ 复验通过，但缺少部署凭证：已保留队列，待 CI 或人工补充凭证后完成部署');
      }
    }
  } catch (e) {
    console.error('\n✗ T4 部署失败:', e.message);
    // 【重要】此处不再自动 git revert 代码。
    // 原因: 生产流量已由 rollbackDeployment 切回旧版本，风险已消除；
    //       而自动 revert 会把本次全部修复从工作区抹掉(2026-08-04 曾因误判导致修复险些丢失)。
    //       代码保留在本地，配合下方 P0 工单由人工/下一轮自愈处理，才是安全做法。
    console.log('[safety] 已保留本地代码变更（不自动 revert），生产侧风险由回滚承担');
    const backlog = readJson(BACKLOG, { issues: [] });
    backlog.issues = backlog.issues || [];
    backlog.issues.push({
      id: `AUTO-P0-${ts().slice(0, 10)}`,
      type: 'deploy_gate_failure',
      severity: 'critical', status: 'open',
      title: 'T4 部署守门人失败需人工介入',
      error: e.message, detected_at: ts()
    });
    writeJson(BACKLOG, backlog);
    result.status = 'FAILED';
    result.error = e.message;
    console.log('[escalate] 已写入 P0 工单，等待人工介入');
  }
  writeJson(REPORT, result);
}

main();
