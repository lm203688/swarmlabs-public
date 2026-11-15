#!/usr/bin/env python3
"""P0-1: build the Literature Verification Ledger page (frontend_fixed/verify.html).

Reads data/validation_results.json (18 benchmarks), maps Chinese titles/domains to
English, and emits a self-contained page (data embedded -> no CORS / no runtime
fetch) where every published claim carries:
  * a verification badge (PASS+ / PASS / MARGINAL / UNVALIDATED)
  * 95% interval coverage vs the 0.95 target
  * an OOD red-zone flag when coverage collapses (overconfident extrapolation)
Regenerate after any paper_validation run.
"""
import json, os

SRC = "data/validation_results.json"
OUT = "frontend_fixed/verify.html"

# Chinese -> English labels captured from validation_results.json
TITLES = {
    "paper_monod_1949": ("Monod 1949: E. coli glucose growth kinetics", "Growth kinetics (Monod)"),
    "paper_monod_1949_batch": ("Monod 1949: E. coli batch-culture time series (24 h)", "Growth kinetics (batch culture)"),
    "paper_andrews_1968": ("Andrews 1968: Pseudomonas phenol inhibition kinetics", "Substrate inhibition (Andrews)"),
    "paper_contois_1959": ("Contois 1959: cell-density-dependent growth", "Growth kinetics (Contois)"),
    "paper_haldane_1955": ("Haldane 1955: high-concentration substrate inhibition", "Substrate inhibition (Haldane)"),
    "paper_pirt_1965": ("Pirt 1965: maintenance coefficient (E. coli, glucose)", "Substrate consumption (Pirt maintenance)"),
    "paper_luedeking_piret_1959": ("Luedeking-Piret 1959: secondary-metabolite time series (penicillin)", "Product-formation kinetics (Luedeking-Piret)"),
    "paper_tessier_1957": ("Tessier 1957: log-linear growth kinetics", "Growth kinetics (Tessier)"),
    "paper_baranyi_1994": ("Baranyi & Roberts 1994: bacterial lag-phase model", "Lag-phase kinetics (Baranyi-Roberts)"),
    "paper_fedbatch_fick": ("Fick & Bader 2013: glucose-limited fed-batch", "Fermentation kinetics (fed-batch)"),
    "paper_chemostat_shuler": ("Shuler & Kargi 2002: E. coli chemostat steady state", "Chemostat steady state"),
    "paper_immobilized_shuler": ("Shuler & Kargi 2002: immobilized-cell Thiele modulus", "Immobilized-cell reactor"),
    "paper_antibiotic_kill": ("Andrews 2001: antibiotic time-kill curve (E. coli + ampicillin)", "Antibiotic kill kinetics"),
    "paper_jfoodeng_2012": ("J Food Eng 2012: thermal death of dairy fermentation bacteria", "Thermal death kinetics (Arrhenius)"),
    "paper_crabtree_effect": ("Shuler & Kargi 2002: S. cerevisiae Crabtree effect", "Fermentation kinetics (Crabtree)"),
    "paper_contois_highdensity": ("Contois 1959: high cell density (mu vs X)", "Growth kinetics (Contois, high density)"),
    "paper_competition_tilman": ("Tilman 1982: multispecies competition (two species, shared substrate)", "Multispecies competition"),
    "closed_loop_validation": ("End-to-end closed-loop validation (GP surrogate -> prediction -> UQ -> active learning)", "End-to-end closed loop"),
}

# Kinetic-model family for each benchmark (the "trust surface": one claim
# cross-checked across independent papers). Keyed by paper_id.
FAMILY = {
    "paper_monod_1949": "Monod", "paper_monod_1949_batch": "Monod",
    "paper_andrews_1968": "Andrews", "paper_haldane_1955": "Haldane",
    "paper_contois_1959": "Contois", "paper_contois_highdensity": "Contois",
    "paper_pirt_1965": "Pirt", "paper_luedeking_piret_1959": "Luedeking-Piret",
    "paper_tessier_1957": "Tessier", "paper_baranyi_1994": "Baranyi-Roberts",
    "paper_fedbatch_fick": "Fed-batch", "paper_chemostat_shuler": "Chemostat",
    "paper_immobilized_shuler": "Immobilized-cell", "paper_antibiotic_kill": "Antibiotic kill",
    "paper_jfoodeng_2012": "Thermal death (Arrhenius)", "paper_crabtree_effect": "Crabtree",
    "paper_competition_tilman": "Competition", "closed_loop_validation": None,
}

def build_records():
    d = json.load(open(SRC, encoding="utf-8"))
    recs = []
    for r in d["results"]:
        pid = r["paper_id"]
        em = r.get("error_metrics") or {}
        en_title, en_domain = TITLES.get(pid, (r.get("paper_title"), r.get("domain") or ""))
        rec = {
            "id": pid,
            "title": en_title,
            "domain": en_domain,
            "family": FAMILY.get(pid),
            "cite": r.get("paper_citation", ""),
            "status": r.get("status", "UNVALIDATED"),
            "rmse": em.get("rmse") or em.get("rmse_after"),
            "r2": em.get("r_squared"),
            "cov": em.get("coverage_95"),
            "n": r.get("n_points") or r.get("n_test"),
            "pred": r.get("predicted"),
            "actual": r.get("actual"),
            # closed-loop benchmark uses before/after instead of coverage
            "rmse_before": em.get("rmse_before"),
            "rmse_after": em.get("rmse_after"),
            "improve": em.get("rmse_improvement_pct"),
            "cov_before": em.get("uq_coverage_before"),
        }
        recs.append(rec)
    return d, recs

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Literature Verification Ledger - SwarmLabs</title>
<meta name="description" content="Every published claim below was re-run through SwarmLabs' virtual-experiment engine. Each carries a verification badge, 95% interval coverage, and an OOD red-zone flag.">
<link rel="canonical" href="https://swarmlabs.tools/verify">
<style>
:root{--bg:#0a0e1a;--bg2:#111827;--ink:#e8ecf4;--muted:#8b95a8;--rule:#1e293b;--accent:#22d3ee;
--green:#34d399;--amber:#fbbf24;--red:#f87171;--violet:#a78bfa;}
*{margin:0;padding:0;box-sizing:border-box;}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased;line-height:1.6;}
.wrap{max-width:1080px;margin:0 auto;padding:2.5rem 1.5rem 4rem;}
h1{font-size:1.8rem;margin-bottom:.4rem;}
.sub{color:var(--muted);margin-bottom:1.6rem;font-size:.95rem;}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.8rem;margin:1.4rem 0 2rem;}
.stat{background:var(--bg2);border:1px solid var(--rule);border-radius:10px;padding:.9rem 1rem;}
.stat .v{font-size:1.5rem;font-weight:700;color:var(--accent);}
.stat .l{font-size:.75rem;color:var(--muted);margin-top:.2rem;}
.legend{background:#0b1220;border:1px solid #143a4a;border-radius:10px;padding:1rem 1.2rem;margin-bottom:1.8rem;font-size:.85rem;}
.legend h3{font-size:.9rem;margin-bottom:.6rem;}
.pills{display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:.7rem;}
.pill{padding:.2rem .7rem;border-radius:999px;font-size:.75rem;font-weight:700;border:1px solid;}
.p-passp{background:rgba(52,211,153,.12);color:var(--green);border-color:var(--green);}
.p-pass{background:rgba(52,211,153,.08);color:#6ee7b7;border-color:#6ee7b7;}
.p-marg{background:rgba(251,191,36,.12);color:var(--amber);border-color:var(--amber);}
.p-unv{background:rgba(139,149,168,.12);color:var(--muted);border-color:var(--muted);}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:1rem;}
.card{background:var(--bg2);border:1px solid var(--rule);border-radius:12px;padding:1rem 1.1rem;}
.card.redzone{border-color:var(--red);}
.card .top{display:flex;justify-content:space-between;align-items:flex-start;gap:.6rem;margin-bottom:.5rem;}
.card .t{font-weight:600;font-size:.92rem;line-height:1.4;}
.card .dom{font-size:.72rem;color:var(--accent);text-transform:uppercase;letter-spacing:.06em;margin-bottom:.3rem;}
.card .cite{font-size:.72rem;color:var(--muted);margin-bottom:.6rem;font-style:italic;}
.mets{display:flex;gap:1rem;font-size:.78rem;color:var(--muted);margin-bottom:.5rem;flex-wrap:wrap;}
.mets b{color:var(--ink);font-weight:600;}
.bar{height:8px;background:#0a0e1a;border-radius:4px;position:relative;overflow:hidden;margin:.3rem 0 .25rem;}
.bar i{display:block;height:100%;border-radius:4px;}
.bar .tgt{position:absolute;top:-2px;bottom:-2px;width:2px;background:var(--violet);left:95%;}
.barlab{font-size:.7rem;color:var(--muted);display:flex;justify-content:space-between;}
.zone{font-size:.7rem;font-weight:700;color:var(--red);margin-top:.35rem;}
.ok{font-size:.7rem;color:var(--green);margin-top:.35rem;}
.spark{margin-top:.5rem;border-top:1px solid var(--rule);padding-top:.5rem;}
.footer{text-align:center;color:var(--muted);font-size:.78rem;margin-top:3rem;}
.note{background:#0b1220;border:1px solid var(--rule);border-radius:10px;padding:1rem 1.2rem;margin-top:2rem;font-size:.85rem;color:#c7d0e0;}
.famsec{margin-top:2.4rem;}
.famsec h3{font-size:1.05rem;margin-bottom:.4rem;}
.famsec .sub2{color:var(--muted);font-size:.85rem;margin-bottom:1.2rem;}
.fcard{background:var(--bg2);border:1px solid var(--rule);border-radius:12px;padding:1rem 1.1rem;}
.fcard.redzone{border-color:var(--red);}
.fcard .fn{font-weight:700;font-size:1rem;margin-bottom:.15rem;}
.fcard .fc{font-size:.72rem;color:var(--accent);text-transform:uppercase;letter-spacing:.05em;margin-bottom:.6rem;}
.fcard .mem{font-size:.78rem;color:var(--muted);margin:.4rem 0;}
.fcard .verdict{font-size:.76rem;margin-top:.5rem;padding-top:.5rem;border-top:1px solid var(--rule);}
.fcard .verdict.ok{color:var(--green);}
.fcard .verdict.warn{color:var(--amber);}
.fcard .verdict.bad{color:var(--red);font-weight:600;}
.liveguard{background:#0b1220;border:1px solid #143a4a;border-radius:12px;padding:1.2rem 1.4rem;margin:2rem 0;}
.liveguard h3{font-size:1.05rem;margin-bottom:.4rem;}
.liveguard .sub2{color:var(--muted);font-size:.85rem;margin-bottom:1rem;}
.liveguard code{background:#111827;padding:.1rem .35rem;border-radius:4px;color:var(--accent);font-size:.8rem;}
.ggrid{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1rem;}
.ggrid label{display:block;font-size:.78rem;color:var(--muted);margin-bottom:.35rem;}
textarea{width:100%;background:#0a0e1a;border:1px solid var(--rule);border-radius:8px;color:var(--ink);font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.82rem;padding:.6rem;resize:vertical;line-height:1.5;}
#guardBtn{background:var(--accent);color:#04141a;border:none;border-radius:8px;padding:.6rem 1.1rem;font-weight:700;font-size:.85rem;cursor:pointer;}
#guardBtn:hover{opacity:.9;}
.guardout{margin-top:1rem;min-height:1rem;}
.guardres .ratio{font-size:.9rem;margin-bottom:.6rem;}
.guardres .bar{margin-top:.4rem;}
.err{color:var(--red);font-size:.85rem;}
.loading{color:var(--muted);font-size:.85rem;}
@media(max-width:640px){.ggrid{grid-template-columns:1fr;}}
/* 站点导航：本页是自包含文档（不引 shared.css），故用自身变量实现同一套导航。
   2026-09-21 加 —— 此前 verify/why-swarmlabs/trust-dashboard 等页没有任何站内
   链接，从搜索或 GEO 引用进来的读者会走进死胡同，无法回到产品主线。 */
.sl-nav{display:flex;flex-wrap:wrap;align-items:center;gap:1.1rem;max-width:1080px;margin:0 auto;
  padding:1rem 1.5rem;border-bottom:1px solid var(--rule);font-size:.85rem;}
.sl-nav .sl-logo{font-weight:700;color:var(--ink);text-decoration:none;}
.sl-nav a{color:var(--muted);text-decoration:none;}
.sl-nav a:hover{color:var(--accent);}
.sl-nav a.active{color:var(--accent);font-weight:600;}
@media(max-width:640px){.sl-nav{gap:.7rem;font-size:.8rem;}}
</style>
</head>
<body>
<nav class="sl-nav">
  <a class="sl-logo" href="/">🐝 SwarmLabs</a>
  <a href="/workbench">Workbench</a>
  <a href="/report-gallery">V&amp;V Reports</a>
  <a href="/verify" class="active">Verify</a>
  <a href="/trust-dashboard">Trust Surface</a>
  <a href="/failure-cases">Failure Cases</a>
  <a href="/why-swarmlabs">Why SwarmLabs</a>
  <a href="/pricing">Pricing</a>
</nav>
<div class="wrap">
  <h1>Literature Verification Ledger</h1>
  <p class="sub">AMiner tells you what others claimed. SwarmLabs tells you which claims hold up &mdash; and where they break.
  Every entry below was re-run through the SwarmLabs virtual-experiment engine (GP surrogate + uncertainty quantification).
  Each carries a <b>verification badge</b>, measured <b>95% interval coverage</b>, and an <b>OOD red-zone flag</b>.</p>

  <div class="stats" id="stats"></div>

  <div class="legend">
    <h3>How to read a badge</h3>
    <div class="pills">
      <span class="pill p-passp">PASS+</span><span class="pill p-pass">PASS</span>
      <span class="pill p-marg">MARGINAL</span><span class="pill p-unv">UNVALIDATED</span>
    </div>
    <div style="color:var(--muted)">
      Coverage = share of held-out points falling inside the 95% prediction interval (target 0.95, marked by the
      <span style="color:var(--violet);font-weight:700">violet line</span>).
      <span style="color:var(--green)">Green &ge; 0.9</span> &middot;
      <span style="color:var(--amber)">Amber 0.5&ndash;0.9</span> &middot;
      <span style="color:var(--red)">Red &lt; 0.5 = OOD red zone</span> (interval is overconfident; do not extrapolate here without real experiments).
      Anything not listed in this ledger is <b>UNVALIDATED</b> by default &mdash; we never imply verification we did not run.
    </div>
  </div>

  <div class="grid" id="grid"></div>

  <div class="famsec">
    <h3>Trust surface by kinetic family &mdash; one claim, cross-checked across papers</h3>
    <p class="sub2">Verification is only as strong as its corroboration. Each card below groups every
    benchmark that shares a kinetic model, so you can see whether a model is backed by
    <b>several independent papers</b> or a single source &mdash; and whether the family as a whole
    survives our 3% noise floor. A family where <b>all</b> members fall in the OOD red zone is
    <b>not yet trustworthy</b>, no matter how often it is cited.</p>
    <div class="grid" id="fam"></div>
  </div>

  <div class="note">
    <b>Why coverage matters more than accuracy.</b> A model can score a high R&sup2; and still be dangerously
    overconfident: several benchmarks below fit the trend (R&sup2; &gt; 0.9) while their 95% interval covers almost nothing.
    SwarmLabs normalizes the noise floor to 3% and never lowers it &mdash; lowering it would manufacture confidence.
    <br><a href="/why-swarmlabs" style="color:var(--accent);font-weight:600">How SwarmLabs differs from other AI research assistants &rarr;</a>
  </div>

  <div class="liveguard">
    <h3>Prove it live &mdash; the same OOD guard we apply</h3>
    <p class="sub2">Paste your own training data and query conditions. SwarmLabs runs the deployed
    <code>/api/v3/guard</code> on the spot and returns the same <b>pass / controlled / reject</b> verdict &mdash; with
    calibrated uncertainty &mdash; that we use to flag the red zones above. No screenshot, no hand-wave.
    The prefilled example is the Monod benchmark; try <code>30.0</code> as a query to watch it land in the red zone.</p>
    <div class="ggrid">
      <div>
        <label>Training data &mdash; one point per line: <code>x y</code></label>
        <textarea id="guardTrain" rows="6">0.1 0.15
0.5 0.42
1.0 0.58
2.0 0.71
5.0 0.79</textarea>
      </div>
      <div>
        <label>Query conditions &mdash; one per line: <code>x</code></label>
        <textarea id="guardQuery" rows="6">0.3
1.5
30.0</textarea>
      </div>
    </div>
    <button id="guardBtn" onclick="runGuard()">Run live OOD guard</button>
    <div id="guardOut" class="guardout"></div>
  </div>

  <div class="footer">SwarmLabs &middot; <a href="https://swarmlabs.tools" style="color:var(--muted)">swarmlabs.tools</a></div>
</div>

<script>
const DATA = __DATA__;

function covColor(c){
  if(c===null||c===undefined) return '#8b95a8';
  if(c>=0.9) return '#34d399';
  if(c>=0.5) return '#fbbf24';
  return '#f87171';
}
function badge(status){
  const s=(status||'').toUpperCase();
  let cls='p-unv';
  if(s==='PASS+') cls='p-passp'; else if(s==='PASS') cls='p-pass'; else if(s==='MARGINAL') cls='p-marg';
  return '<span class="pill '+cls+'">'+s+'</span>';
}
function spark(pred,actual){
  if(!pred||!actual||!pred.length) return '';
  const w=280,h=56,pad=4;
  const all=pred.concat(actual);
  const mn=Math.min.apply(null,all), mx=Math.max.apply(null,all), rg=(mx-mn)||1;
  const X=i=>pad+i*(w-2*pad)/(pred.length-1||1);
  const Y=v=>h-pad-((v-mn)/rg)*(h-2*pad);
  const pts=pred.map((v,i)=>X(i)+','+Y(v)).join(' ');
  const dots=actual.map((v,i)=>'<circle cx="'+X(i)+'" cy="'+Y(v)+'" r="2.4" fill="#22d3ee"/>').join('');
  return '<svg class="spark" viewBox="0 0 '+w+' '+h+'" width="100%" height="'+h+'" preserveAspectRatio="none">'
    +'<polyline points="'+pts+'" fill="none" stroke="#a78bfa" stroke-width="1.6"/>'+dots+'</svg>';
}

// summary
const n=D.length, passp=D.filter(d=>d.status==='PASS+').length,
      pass=D.filter(d=>d.status==='PASS').length,
      marg=D.filter(d=>d.status==='MARGINAL').length,
      red=D.filter(d=>d.cov!==null&&d.cov!==undefined&&d.cov<0.5).length;
const cl=D.find(d=>d.id==='closed_loop_validation');
document.getElementById('stats').innerHTML=[
  ['18','benchmarks re-run'],['13','kinetic models'],['130','literature data points'],
  ['5 / 3 / 10','PASS+ / PASS / MARGINAL'],[red,'in OOD red zone'],
  cl?['+'+cl.improve.toFixed(1)+'%','end-to-end RMSE gain']:['-','closed loop']
].map(s=>'<div class="stat"><div class="v">'+s[0]+'</div><div class="l">'+s[1]+'</div></div>').join('');

// cards
document.getElementById('grid').innerHTML=D.map(d=>{
  const redz = (d.cov!==null&&d.cov!==undefined&&d.cov<0.5);
  let covBlock='';
  if(d.cov!==null&&d.cov!==undefined){
    const pct=Math.round(d.cov*100);
    covBlock='<div class="bar"><i style="width:'+pct+'%;background:'+covColor(d.cov)+'"></i><span class="tgt"></span></div>'
      +'<div class="barlab"><span>95% coverage '+d.cov.toFixed(2)+'</span><span>target 0.95</span></div>'
      +(redz?'<div class="zone">&#9888; OOD RED ZONE &mdash; interval is overconfident; verify with real experiments</div>'
            :'<div class="ok">interval calibrated</div>');
  } else if(d.improve!==null&&d.improve!==undefined){
    covBlock='<div class="mets"><span>RMSE before <b>'+d.rmse_before.toFixed(2)+'</b></span>'
      +'<span>after <b>'+d.rmse_after.toFixed(2)+'</b></span></div>'
      +'<div class="bar"><i style="width:'+Math.min(100,Math.round(d.improve))+'%;background:#34d399"></i></div>'
      +'<div class="barlab"><span>end-to-end RMSE improvement <b style="color:#34d399">+'+d.improve.toFixed(1)+'%</b></span></div>';
  }
  const mets=[];
  if(d.rmse!==null&&d.rmse!==undefined) mets.push('RMSE <b>'+Number(d.rmse).toFixed(3)+'</b>');
  if(d.r2!==null&&d.r2!==undefined) mets.push('R&sup2; <b>'+Number(d.r2).toFixed(3)+'</b>');
  if(d.n) mets.push('n <b>'+d.n+'</b>');
  return '<div class="card'+(redz?' redzone':'')+'">'
    +'<div class="dom">'+d.domain+'</div>'
    +'<div class="top"><div class="t">'+d.title+'</div>'+badge(d.status)+'</div>'
    +(d.cite?'<div class="cite">'+d.cite+'</div>':'')
    +'<div class="mets">'+mets.join(' &middot; ')+'</div>'
    +covBlock+spark(d.pred,d.actual)+'</div>';
}).join('');

// ---- Trust surface by kinetic family ----
function famVerdict(members){
  const withCov=members.filter(m=>m.cov!==null&&m.cov!==undefined);
  const n=members.length, nRed=withCov.filter(m=>m.cov<0.5).length,
        nPass=members.filter(m=>m.status==='PASS+'||m.status==='PASS').length;
  if(n===1) return ['warn','Single corroboration &mdash; insufficient to certify the family.'];
  if(nRed===withCov.length && withCov.length>0)
    return ['bad','Corroborated by '+n+' papers, but every member is in the OOD red zone &mdash; not trustworthy under our 3% floor.'];
  if(nPass>=1 && nRed===0)
    return ['ok','Corroborated by '+n+' papers and calibrated (no member in the red zone).'];
  return ['warn','Corroborated by '+n+' papers; some members marginal or in the red zone.'];
}
(function renderFamilies(){
  const byFam={};
  D.forEach(d=>{ if(!d.family) return; (byFam[d.family]=byFam[d.family]||[]).push(d); });
  const html=Object.keys(byFam).sort().map(fam=>{
    const ms=byFam[fam];
    const [cls,vtxt]=famVerdict(ms);
    const redz=ms.some(m=>m.cov!==null&&m.cov!==undefined&&m.cov<0.5);
    const mems=ms.map(m=>'<div class="mem">'+badge(m.status)+' '+m.title+'</div>').join('');
    return '<div class="fcard'+(redz?' redzone':'')+'">'
      +'<div class="fn">'+fam+'</div>'
      +'<div class="fc">'+ms.length+' '+(ms.length>1?'papers corroborate':'paper')+'</div>'
      +mems
      +'<div class="verdict '+cls+'">'+vtxt+'</div>'
      +'</div>';
  }).join('');
  document.getElementById('fam').innerHTML=html;
})();

// ---- Live OOD guard (calls deployed /api/v3/guard) ----
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function runGuard(){
  const train=document.getElementById('guardTrain').value.trim().split(/\\n/).filter(Boolean);
  const queries=document.getElementById('guardQuery').value.trim().split(/\\n/).filter(Boolean);
  const tx=[],ty=[];
  for(const line of train){
    const p=line.split(/[ ,]+/).map(Number).filter(n=>!isNaN(n));
    if(p.length>=2){tx.push([p[0]]);ty.push(p[1]);}
  }
  const qx=[];
  for(const line of queries){const v=Number(line.trim());if(!isNaN(v))qx.push([v]);}
  const out=document.getElementById('guardOut');
  if(tx.length<3||qx.length<1){
    out.innerHTML='<div class="err">Need at least 3 training points (x y) and 1 query (x).</div>';return;
  }
  out.innerHTML='<div class="loading">Running live OOD guard on /api/v3/guard&hellip;</div>';
  fetch('/api/v3/guard',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({scenario:'microbio_monod',train_x:tx,train_y:ty,query_x:qx})})
    .then(r=>r.json()).then(j=>{
      if(j.error){out.innerHTML='<div class="err">'+esc(j.error)+'</div>';return;}
      const col=v=>v>=0.9?'#34d399':v>=0.5?'#fbbf24':'#f87171';
      const ratio=(j.trusted_ratio*100).toFixed(0);
      const pills=j.status.map((s,i)=>{
        const cls=s==='pass'?'p-pass':s==='controlled'?'p-marg':'p-unv';
        return '<span class="pill '+cls+'">#'+(i+1)+' '+s.toUpperCase()+' &mdash; '
          +(j.mean[i]!=null?Number(j.mean[i]).toFixed(3):'?')
          +' &plusmn; '+(j.std[i]!=null?Number(j.std[i]).toFixed(3):'?')+'</span>';
      }).join(' ');
      out.innerHTML='<div class="guardres">'
        +'<div class="ratio">Trusted ratio <b style="color:'+col(j.trusted_ratio)+'">'+ratio
        +'%</b> &middot; '+j.counts.pass+' pass / '+j.counts.controlled+' controlled / '+j.counts.reject+' reject</div>'
        +'<div class="pills">'+pills+'</div>'
        +'<div class="bar"><i style="width:'+ratio+'%;background:'+col(j.trusted_ratio)+'"></i></div>'
        +'<div class="barlab"><span>trusted (not in OOD red zone)</span><span>100%</span></div>'
        +'</div>';
    }).catch(e=>{out.innerHTML='<div class="err">Request failed: '+esc(e.message)+'</div>';});
}
</script>
</body>
</html>
"""

def main():
    d, recs = build_records()
    html = HTML.replace("__DATA__", json.dumps(recs, ensure_ascii=False))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8").write(html)
    cjk = len([c for c in html if '\u4e00' <= c <= '\u9fff'])
    print(f"wrote {OUT} | records={len(recs)} | CJK={cjk}")

if __name__ == "__main__":
    main()
