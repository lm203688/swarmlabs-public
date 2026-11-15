/**
 * SwarmLabs Creem 商店监控脚本
 * 定时查询 Creem API，监控交易、订阅、客户状态变化
 * 输出报告到 command-center/04-finance/ 目录
 * 
 * 用法: node scripts/creem_store_monitor.js
 * 定时: 每日 01:15 (财务层闭环) — 通过 TRAE Schedule 触发
 */

const fs = require('fs');
const path = require('path');
const https = require('https');

// ===== 配置 =====
const CREEM_API_KEY = process.env.CREEM_API_KEY;  // 必须从环境变量注入，禁止硬编码生产密钥
if (!CREEM_API_KEY) {
  console.error('[SECURITY] 未设置 CREEM_API_KEY 环境变量。拒绝携带内置密钥运行。');
  process.exit(2);
}
const API_BASE = 'api.creem.io';
const REPORT_DIR = path.join(__dirname, '..', 'command-center', '04-finance');
const STATE_FILE = path.join(REPORT_DIR, 'creem-heartbeat-state.json');

// SwarmLabs 产品映射（与 [[path]].js 一致）
const PRODUCT_MAP = {
  'prod_22YhSbYonX9hiC0OppnXTn': { name: '免费体验', type: 'subscription', plan: 'free', credits: 50 },
  'prod_4EpFVQGKm5vWXChbRiFdbE': { name: 'Pro月付', type: 'subscription', plan: 'pro', credits: 2000 },
  'prod_pny43rzDa0mmBaj7d9k4w': { name: 'Team协作', type: 'subscription', plan: 'team', credits: 5000 },
  'prod_5IooNCEQoCyqp758oeVPGT': { name: '终身会员', type: 'subscription', plan: 'enterprise', credits: 10000 },
  'prod_5OFcAcJeXzfTMkDDt6woBh': { name: '5000积分包', type: 'credits', credits: 5000 },
  'prod_44o1TOBce0Zt00X4E5ACET': { name: '2000积分包', type: 'credits', credits: 2000 },
  'prod_sXzYf1nsR6NhV3JjTvoZd': { name: '入门包(100积分)', type: 'credits', credits: 100 },
  'prod_4vuFgIUtUABJ0Zojkzm7EC': { name: '标准包(500积分)', type: 'credits', credits: 500 },
};

// ===== HTTP 请求封装 =====
function apiRequest(method, apiPath) {
  return new Promise((resolve) => {
    const options = {
      hostname: API_BASE,
      path: '/v1' + apiPath,
      method: method,
      headers: {
        'x-api-key': CREEM_API_KEY,
        'Content-Type': 'application/json'
      }
    };
    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try { resolve({ status: res.statusCode, data: JSON.parse(data) }); }
        catch(e) { resolve({ status: res.statusCode, data: null, raw: data.substring(0, 500) }); }
      });
    });
    req.on('error', (e) => resolve({ error: e.message }));
    req.setTimeout(15000, () => { req.destroy(); resolve({ error: 'timeout' }); });
    req.end();
  });
}

// ===== 状态管理 =====
function loadState() {
  try {
    if (fs.existsSync(STATE_FILE)) {
      return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
    }
  } catch(e) {}
  return {
    lastCheckAt: null,
    lastTransactionId: null,
    transactionCount: 0,
    customerCount: 0,
    subscriptions: { active: 0, trialing: 0, past_due: 0, paused: 0, canceled: 0, expired: 0, scheduled_cancel: 0 },
    knownSubscriptions: {}
  };
}

function saveState(state) {
  if (!fs.existsSync(REPORT_DIR)) {
    fs.mkdirSync(REPORT_DIR, { recursive: true });
  }
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
}

// ===== 主监控逻辑 =====
async function main() {
  const today = new Date().toISOString().split('T')[0];
  const now = new Date().toISOString();
  const prevState = loadState();
  
  console.log('='.repeat(60));
  console.log(`SwarmLabs Creem 商店监控 — ${now}`);
  console.log('='.repeat(60));
  
  const report = {
    check_time: now,
    store: 'SwarmLabs (sto_7gBcCekvUKTpsaAFyf)',
    products_monitored: Object.keys(PRODUCT_MAP).length,
    alerts: [],
    transactions: { total: 0, new_since_last: 0, revenue_today_cents: 0, items: [] },
    subscriptions: { active: 0, trialing: 0, past_due: 0, paused: 0, canceled: 0, expired: 0, scheduled_cancel: 0, items: [] },
    customers: { total: 0, new_since_last: 0 },
    product_sales: {}
  };
  
  // 1. 查询交易记录
  console.log('\n📊 查询交易记录...');
  const txResp = await apiRequest('GET', '/transactions/search');
  if (txResp.data && txResp.data.items) {
    const transactions = txResp.data.items;
    report.transactions.total = transactions.length;
    report.transactions.new_since_last = prevState.lastTransactionId 
      ? transactions.filter(t => t.id !== prevState.lastTransactionId).length 
      : transactions.length;
    
    // 计算今日收入
    const todayStr = today;
    transactions.forEach(t => {
      const txDate = (t.created_at || '').split('T')[0];
      if (txDate === todayStr && t.status === 'paid') {
        report.transactions.revenue_today_cents += t.amount || 0;
      }
      // 产品销售统计
      const prodId = t.product;
      if (prodId) {
        if (!report.product_sales[prodId]) {
          report.product_sales[prodId] = { name: PRODUCT_MAP[prodId]?.name || 'Unknown', count: 0, revenue_cents: 0 };
        }
        report.product_sales[prodId].count++;
        if (t.status === 'paid') report.product_sales[prodId].revenue_cents += t.amount || 0;
      }
    });
    
    // 新交易详情（最多10条）
    const newTx = prevState.lastTransactionId 
      ? transactions.filter(t => t.id !== prevState.lastTransactionId).slice(0, 10)
      : transactions.slice(0, 10);
    report.transactions.items = newTx.map(t => ({
      id: t.id,
      amount: t.amount,
      currency: t.currency,
      status: t.status,
      product: PRODUCT_MAP[t.product]?.name || t.product,
      customer: t.customer_email || 'unknown',
      date: t.created_at
    }));
    
    if (report.transactions.new_since_last > 0) {
      report.alerts.push({
        type: 'new_transactions',
        severity: 'info',
        message: `发现 ${report.transactions.new_since_last} 笔新交易`
      });
    }
    
    // 更新 lastTransactionId
    if (transactions.length > 0) {
      prevState.lastTransactionId = transactions[0].id;
    }
    prevState.transactionCount = transactions.length;
    
    console.log(`  总交易: ${transactions.length}, 新增: ${report.transactions.new_since_last}, 今日收入: $${(report.transactions.revenue_today_cents/100).toFixed(2)}`);
  } else {
    console.log(`  ⚠️ 交易查询失败: ${txResp.error || txResp.status}`);
    report.alerts.push({ type: 'api_error', severity: 'warn', message: '交易查询失败' });
  }
  
  // 2. 查询订阅状态
  console.log('\n📋 查询订阅状态...');
  const subResp = await apiRequest('GET', '/subscriptions/search');
  const subs = (subResp.data && (subResp.data.items || subResp.data)) || [];
  if (Array.isArray(subs) && subs.length >= 0) {
    subs.forEach(s => {
      const status = s.status || 'unknown';
      if (report.subscriptions[status] !== undefined) report.subscriptions[status]++;
      
      // 检查状态变化
      const prevSub = prevState.knownSubscriptions[s.id];
      if (prevSub && prevSub.status !== status) {
        report.alerts.push({
          type: 'subscription_status_change',
          severity: status === 'canceled' || status === 'expired' || status === 'past_due' ? 'warning' : 'info',
          message: `订阅 ${s.id} 状态变更: ${prevSub.status} → ${status} (产品: ${PRODUCT_MAP[s.product?.id]?.name || 'Unknown'})`
        });
      }
      prevState.knownSubscriptions[s.id] = { status, product: s.product?.id };
    });
    
    report.subscriptions.items = subs.slice(0, 20).map(s => ({
      id: s.id,
      status: s.status,
      product: PRODUCT_MAP[s.product?.id]?.name || 'Unknown',
      customer: s.customer?.email || 'unknown',
      current_period_end: s.current_period_end_date
    }));
    
    Object.keys(report.subscriptions).forEach(k => {
      if (k !== 'items') prevState.subscriptions[k] = report.subscriptions[k];
    });
    
    console.log(`  活跃: ${report.subscriptions.active}, 过期: ${report.subscriptions.expired}, 取消: ${report.subscriptions.canceled}, 逾期: ${report.subscriptions.past_due}`);
    
    if (report.subscriptions.past_due > 0) {
      report.alerts.push({ type: 'payment_issues', severity: 'warning', message: `${report.subscriptions.past_due} 个订阅支付逾期` });
    }
  } else {
    console.log(`  ⚠️ 订阅查询失败: ${subResp.error || subResp.status}`);
  }
  
  // 3. 查询客户数量
  console.log('\n👥 查询客户...');
  const custResp = await apiRequest('GET', '/customers/list');
  const custList = (custResp.data && (custResp.data.items || (Array.isArray(custResp.data) ? custResp.data : []))) || [];
  if (Array.isArray(custList)) {
    report.customers.total = custList.length;
    report.customers.new_since_last = prevState.customerCount 
      ? Math.max(0, custList.length - prevState.customerCount) 
      : custList.length;
    prevState.customerCount = custList.length;
    console.log(`  总客户: ${custList.length}, 新增: ${report.customers.new_since_last}`);
  } else {
    console.log(`  ⚠️ 客户查询返回格式异常: ${custResp.status}`);
  }
  
  // 4. 保存状态
  prevState.lastCheckAt = now;
  saveState(prevState);
  
  // 5. 生成报告
  report.state_saved = true;
  report.next_check = '明日 01:15 (财务层闭环)';
  
  const reportFile = path.join(REPORT_DIR, `creem-store-monitor-${today}.json`);
  if (!fs.existsSync(REPORT_DIR)) {
    fs.mkdirSync(REPORT_DIR, { recursive: true });
  }
  fs.writeFileSync(reportFile, JSON.stringify(report, null, 2));
  
  // 6. 输出摘要
  console.log('\n' + '='.repeat(60));
  console.log('📊 商店监控摘要');
  console.log('='.repeat(60));
  console.log(`交易总数: ${report.transactions.total} (新增 ${report.transactions.new_since_last})`);
  console.log(`今日收入: $${(report.transactions.revenue_today_cents/100).toFixed(2)}`);
  console.log(`活跃订阅: ${report.subscriptions.active}`);
  console.log(`总客户: ${report.customers.total} (新增 ${report.customers.new_since_last})`);
  console.log(`告警: ${report.alerts.length} 条`);
  
  if (report.alerts.length > 0) {
    console.log('\n⚠️ 告警详情:');
    report.alerts.forEach(a => {
      const icon = a.severity === 'warning' ? '🔴' : a.severity === 'info' ? '🟢' : '🟡';
      console.log(`  ${icon} [${a.type}] ${a.message}`);
    });
  }
  
  console.log(`\n📄 报告已保存: ${reportFile}`);
  console.log(`💾 状态已保存: ${STATE_FILE}`);
  
  return report;
}

main().catch(err => {
  console.error('❌ 监控脚本异常:', err);
  process.exit(1);
});
