/**
 * SwarmLabs JavaScript / TypeScript SDK（零依赖，浏览器/Node 通用）
 * 用法:
 *   import { SwarmLabs } from './swarmlabs.js';
 *   const client = new SwarmLabs({ baseUrl: 'https://swarmlabs.tools', token: 'stk_xxx' });
 *   const r = await client.run('suzuki', { temperature_C: 80, catalyst_loading: 0.02, time_h: 4 });
 *   console.log(r.result, r.confidence_grade, r.uncertainty, r.literature_support);
 */
export class SwarmLabs {
  constructor(opts = {}) {
    this.base = (opts.baseUrl || 'https://swarmlabs.tools').replace(/\/$/, '');
    this.token = opts.token || null;
  }

  _headers(json) {
    const h = { Accept: 'application/json', 'User-Agent': 'SwarmLabs-JS-SDK/3.4.0' };
    if (json) h['Content-Type'] = 'application/json';
    if (this.token) h['Authorization'] = 'Bearer ' + this.token;
    return h;
  }

  async _req(method, path, body) {
    const url = `${this.base}/${path.replace(/^\//, '')}`;
    const res = await fetch(url, {
      method,
      headers: this._headers(body !== undefined),
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return { _error: res.status, ...data };
    return data;
  }

  run(engine, params = {}) { return this._req('POST', `api/v2/run/${engine}`, params); }
  sweep(engine, { sweep_param = 'temperature_C', points = 10, fixed = {} } = {}) {
    return this._req('POST', 'api/v2/sweep', { engine, sweep_param, points, fixed });
  }
  literature(engine) { return this._req('GET', `api/v2/literature${engine ? '/' + engine : ''}`); }
  negativeSamples(engine) { return this._req('GET', `api/v2/negative-samples${engine ? '/' + engine : ''}`); }
  enterprise() { return this._req('GET', 'api/v2/enterprise'); }
  account() { return this._req('GET', 'api/v2/account'); }
  submitFeedback(engine, predicted, actual, { signal, context } = {}) {
    const body = { engine, predicted, actual };
    if (signal) body.signal = signal;
    if (context) body.context = context;
    return this._req('POST', 'api/v2/feedback', body);
  }
  feedbackSummary() { return this._req('GET', 'api/v2/feedback/summary'); }
  experimentPlan(engine, fixed = {}) { return this._req('POST', `api/v2/experiment-plan/${engine}`, fixed); }
  graph(engine = null) { return this._req('GET', engine ? `api/v2/graph/${engine}` : 'api/v2/graph'); }
  physicsInformed(engine, params = {}) { return this._req('POST', `api/v2/pi/${engine}`, params); }
  suggest(engine) { return this._req('GET', `api/v2/suggest/${engine}`); }
  modelState(engine) { return this._req('GET', `api/v2/model/${engine}`); }
  multifidelity(engine, { smiles, deltaG, method } = {}) {
    if (smiles == null && deltaG == null) return this._req('GET', `api/v2/multifidelity/${engine}`);
    return this._req('POST', 'api/v2/multifidelity', { engine, smiles, deltaG, method });
  }

  // ---- 用户闭环：数据飞轮 / 积分 / 自带API（差异化能力） ----
  myExperiments(engine = null) {
    return this._req('GET', engine ? `api/v2/my-experiments/${engine}` : 'api/v2/my-experiments');
  }
  reapply(targetEngine, { sourceId, sourceEngine, useMyExperiments = false } = {}) {
    const body = { target_engine: targetEngine };
    if (sourceId) body.source_id = sourceId;
    else if (useMyExperiments) { body.use_my_experiments = true; if (sourceEngine) body.source_engine = sourceEngine; }
    return this._req('POST', 'api/v2/reapply', body);
  }
  leaderboard() { return this._req('GET', 'api/v2/leaderboard'); }
  getApikey() { return this._req('GET', 'api/v2/account/apikey'); }
  saveApikey(provider, apiKey, { model, baseUrl } = {}) {
    const body = { provider, apiKey };
    if (model) body.model = model;
    if (baseUrl) body.baseUrl = baseUrl;
    return this._req('POST', 'api/v2/account/apikey', body);
  }
  deleteApikey() { return this._req('POST', 'api/v2/account/apikey', { action: 'delete' }); }
  llm(messages, { model } = {}) {
    const body = { messages };
    if (model) body.model = model;
    return this._req('POST', 'api/v2/llm', body);
  }
}

// Node 环境可直接演示
if (typeof require !== 'undefined' && require.main === module) {
  (async () => {
    const c = new SwarmLabs();
    console.log('suzuki:', await c.run('suzuki', { temperature_C: 80, catalyst_loading: 0.02, time_h: 4 }));
  })();
}
