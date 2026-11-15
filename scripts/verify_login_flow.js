/**
 * 登录/注册流程回归测试（jsdom，无需真实浏览器）
 *
 * 覆盖 FIX-2026-0909-D 修复的三处问题：
 *   1) submitRegister 原先用全局 `event`，Firefox/Safari 下 ReferenceError → 点了没反应、不跳转
 *   2) 409 email_exists 时只弹一句英文提示后仍停在弹窗里 → 用户不知道下一步
 *   3) 成功后未写入 sessionStorage 就跳转的隐患
 *
 * 运行：
 *   NODE_PATH=<workspace>/node_modules node scripts/verify_login_flow.js
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

// jsdom 不支持真实导航，且 window.location 不可重定义。
// 因此把源码里的 `window.location.href = '...'` 替换成 `window.__nav('...')` 来捕获跳转意图。
const HTML = fs.readFileSync(
  path.join(__dirname, '..', 'frontend_fixed', 'login.html'), 'utf8')
  .replace(/window\.location\.href\s*=\s*'([^']+)'/g, "window.__nav('$1')");

function makeWindow(fetchImpl) {
  const dom = new JSDOM(HTML, {
    runScripts: 'dangerously',
    url: 'https://swarmlabs.tools/login',
    beforeParse(window) {
      window.__navTo = null;                       // 跳转目标记录器
      window.__nav = function (u) { window.__navTo = u; };
      window.fetch = fetchImpl;
      // 模拟 Firefox / Safari：不存在全局 event（修复前的代码会在此条件下崩溃）
      try { delete window.event; } catch (e) { /* noop */ }
    },
  });
  return dom.window;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;
function check(name, cond, extra) {
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${extra ? '  → ' + extra : ''}`);
  if (!cond) failures++;
}

(async function main() {
  // ── 场景 1：注册成功（无全局 event 环境）────────────────────────────
  console.log('\n[1] 注册成功 → 应写 token、关弹窗、1s 后跳 /lab');
  const w1 = makeWindow(async () => ({
    json: async () => ({ status: 'success', token: 'stk_test123', user: { plan: 'free', credits: 50 } }),
  }));
  w1.document.getElementById('waitlistEmail').value = 'new@user.com';
  w1.document.getElementById('registerPassword').value = 'abc123';
  w1.submitRegister();
  await sleep(80);
  check('未因缺少全局 event 而抛错（请求已发出）',
    w1.document.getElementById('registerSubmitBtn').textContent === 'Creating...',
    '按钮态=' + w1.document.getElementById('registerSubmitBtn').textContent);
  check('token 写入 sessionStorage',
    w1.sessionStorage.getItem('swarmlabs_token') === 'stk_test123');
  check('注册弹窗已关闭',
    !w1.document.getElementById('registerModal').classList.contains('show'));
  await sleep(1200);
  check('跳转到 /lab', w1.__navTo === '/lab', 'navTo=' + w1.__navTo);

  // ── 场景 2：邮箱已注册（409）──────────────────────────────────────
  console.log('\n[2] 邮箱已注册（409）→ 应关弹窗、回填邮箱、不跳转');
  const w2 = makeWindow(async () => ({
    json: async () => ({ error: 'email_exists', message: 'That email is already registered; sign in instead.' }),
  }));
  w2.document.getElementById('waitlistEmail').value = 'old@user.com';
  w2.document.getElementById('registerPassword').value = 'abc123';
  w2.submitRegister();
  await sleep(80);
  check('弹窗已关闭', !w2.document.getElementById('registerModal').classList.contains('show'));
  check('邮箱已回填到登录框',
    w2.document.getElementById('username').value === 'old@user.com',
    'username=' + w2.document.getElementById('username').value);
  check('按钮已恢复可用',
    w2.document.getElementById('registerSubmitBtn').textContent === 'Create account' &&
    w2.document.getElementById('registerSubmitBtn').disabled === false);
  await sleep(1200);
  check('未跳转（停留在登录页）', w2.__navTo === null, 'navTo=' + w2.__navTo);

  // ── 场景 3：网络/解析失败 ─────────────────────────────────────────
  console.log('\n[3] 请求失败 → 应恢复按钮、不跳转、不写 token');
  const w3 = makeWindow(async () => { throw new Error('network down'); });
  w3.document.getElementById('waitlistEmail').value = 'x@y.com';
  w3.document.getElementById('registerPassword').value = 'abc123';
  w3.submitRegister();
  await sleep(80);
  check('按钮已恢复', w3.document.getElementById('registerSubmitBtn').disabled === false);
  check('未写入 token', w3.sessionStorage.getItem('swarmlabs_token') === null);
  await sleep(1200);
  check('未跳转', w3.__navTo === null, 'navTo=' + w3.__navTo);

  // ── 场景 4：邮箱格式校验 ──────────────────────────────────────────
  console.log('\n[4] 非法输入 → 应直接拒绝，不发请求');
  let called = false;
  const w4 = makeWindow(async () => { called = true; return { json: async () => ({}) }; });
  w4.document.getElementById('waitlistEmail').value = 'not-an-email';
  w4.document.getElementById('registerPassword').value = '123';
  w4.submitRegister();
  await sleep(80);
  check('未发起请求', called === false);

  console.log('\n结论：' + (failures === 0 ? 'ALL PASS' : failures + ' 项 FAIL'));
  process.exit(failures === 0 ? 0 : 1);
})();
