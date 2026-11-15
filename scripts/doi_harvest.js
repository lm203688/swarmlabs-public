#!/usr/bin/env node
/**
 * doi_harvest.js — 从 CrossRef 官方索引采集真实文献，构建 SwarmLabs 文献引用池。
 *
 * 设计原则（诚实优先）:
 *  - DOI 全部来自 CrossRef 返回结果，绝不手写/凭记忆填写；
 *  - 仅采集 journal-article / proceedings-article；
 *  - 不编造实验数值。采集到的文献进入 reference_library（value_extracted=false），
 *    与已完成数值比对的 literature_benchmarks 严格区分统计。
 */
const fs = require('fs');
const path = require('path');

const MAILTO = 'ops@swarmlabs.tools';
const OUT = path.join(__dirname, '..', 'data', 'doi-reference-library.json');

// 覆盖平台 13 个引擎类别的检索主题
const TOPICS = [
  // ---- CFD ----
  ['CFD', 'cylinder_drag', 'drag coefficient circular cylinder Reynolds number experiment'],
  ['CFD', 'vortex_shedding', 'Strouhal number vortex shedding circular cylinder'],
  ['CFD', 'pipe_friction', 'friction factor turbulent pipe flow Colebrook correlation'],
  ['CFD', 'airfoil', 'NACA airfoil lift drag coefficient wind tunnel measurement'],
  ['CFD', 'backward_step', 'backward facing step reattachment length turbulent flow'],
  ['CFD', 'lid_driven_cavity', 'lid driven cavity flow benchmark high Reynolds numbers'],
  ['CFD', 'sphere_drag', 'drag coefficient sphere Reynolds number correlation'],
  ['CFD', 'turbulence_model', 'k-epsilon turbulence model validation benchmark'],
  // ---- HeatTransfer ----
  ['HeatTransfer', 'nusselt_pipe', 'Nusselt number turbulent pipe flow Dittus Boelter correlation'],
  ['HeatTransfer', 'natural_convection', 'natural convection vertical plate Rayleigh number correlation'],
  ['HeatTransfer', 'fin_efficiency', 'heat sink fin efficiency thermal resistance electronics cooling'],
  ['HeatTransfer', 'heat_pipe', 'heat pipe thermal performance experimental study'],
  ['HeatTransfer', 'nanofluid', 'nanofluid thermal conductivity enhancement experimental'],
  ['HeatTransfer', 'boiling', 'pool boiling critical heat flux correlation water'],
  // ---- Structural ----
  ['Structural', 'beam_deflection', 'cantilever beam deflection experimental validation'],
  ['Structural', 'fatigue', 'S-N curve fatigue life steel experimental'],
  ['Structural', 'composite_laminate', 'composite laminate tensile strength experimental characterization'],
  ['Structural', 'buckling', 'column buckling critical load experiment Euler'],
  ['Structural', 'modal_analysis', 'modal analysis natural frequency beam experimental verification'],
  // ---- Energy ----
  ['Energy', 'li_ion_aging', 'lithium ion battery capacity fade cycle aging model'],
  ['Energy', 'nmc_cathode', 'NMC811 cathode cycling performance capacity retention'],
  ['Energy', 'lfp_battery', 'LiFePO4 battery cycle life degradation study'],
  ['Energy', 'perovskite_pce', 'perovskite solar cell power conversion efficiency certified'],
  ['Energy', 'silicon_pv', 'silicon heterojunction solar cell efficiency record'],
  ['Energy', 'pem_fuel_cell', 'PEM fuel cell polarization curve performance model'],
  ['Energy', 'sofc', 'solid oxide fuel cell performance power density experimental'],
  ['Energy', 'supercapacitor', 'supercapacitor specific capacitance electrode experimental'],
  ['Energy', 'electrolyzer', 'water electrolysis hydrogen production efficiency PEM'],
  ['Energy', 'wind_turbine', 'wind turbine power coefficient Betz field measurement'],
  // ---- Chemistry ----
  ['Chemistry', 'suzuki', 'Suzuki Miyaura cross coupling yield optimization palladium'],
  ['Chemistry', 'heck', 'Heck reaction palladium catalyst yield study'],
  ['Chemistry', 'esterification', 'esterification kinetics acid catalyzed conversion equilibrium'],
  ['Chemistry', 'hydrogenation', 'catalytic hydrogenation selectivity pressure effect kinetics'],
  ['Chemistry', 'buchwald', 'Buchwald Hartwig amination yield catalyst screening'],
  ['Chemistry', 'aldol', 'asymmetric aldol reaction enantioselectivity organocatalyst'],
  ['Chemistry', 'grignard', 'Grignard reaction yield optimization study'],
  ['Chemistry', 'click_chemistry', 'copper catalyzed azide alkyne cycloaddition yield kinetics'],
  // ---- Materials ----
  ['Materials', 'sintering', 'ceramic sintering densification relative density temperature'],
  ['Materials', 'corrosion_rate', 'corrosion rate carbon steel salt spray weight loss'],
  ['Materials', 'quantum_dot', 'quantum dot photoluminescence quantum yield synthesis'],
  ['Materials', 'nanoparticle', 'nanoparticle synthesis size control yield characterization'],
  ['Materials', 'polymer_crystallinity', 'polymer crystallinity DSC measurement degree'],
  ['Materials', 'mof_surface_area', 'metal organic framework BET surface area synthesis'],
  ['Materials', 'thin_film', 'thin film deposition thickness uniformity sputtering'],
  ['Materials', 'graphene', 'graphene electrical conductivity CVD growth characterization'],
  ['Materials', 'alloy_hardness', 'alloy hardness heat treatment Vickers experimental'],
  // ---- Environment ----
  ['Environment', 'photocatalysis', 'TiO2 photocatalytic degradation methylene blue kinetics'],
  ['Environment', 'co2_capture', 'CO2 capture amine absorption efficiency experimental'],
  ['Environment', 'adsorption_heavy_metal', 'heavy metal adsorption isotherm Langmuir activated carbon'],
  ['Environment', 'steam_reforming', 'methane steam reforming conversion catalyst experimental'],
  ['Environment', 'wastewater_cod', 'wastewater treatment COD removal efficiency biological'],
  ['Environment', 'air_pollutant', 'NOx removal SCR catalyst conversion efficiency'],
  // ---- Separation ----
  ['Separation', 'ro_membrane', 'reverse osmosis membrane water flux salt rejection'],
  ['Separation', 'nanofiltration', 'nanofiltration membrane rejection permeate flux experimental'],
  ['Separation', 'distillation', 'distillation column separation efficiency tray experimental'],
  ['Separation', 'gas_separation', 'gas separation membrane CO2 CH4 selectivity permeability'],
  ['Separation', 'extraction', 'liquid liquid extraction distribution coefficient experimental'],
  ['Separation', 'crystallization', 'crystallization yield purity cooling experimental study'],
  // ---- Pharma ----
  ['Pharma', 'korsmeyer_peppas', 'Korsmeyer Peppas release exponent HPMC matrix tablet'],
  ['Pharma', 'dissolution', 'drug dissolution profile in vitro release study tablet'],
  ['Pharma', 'bioavailability', 'oral bioavailability pharmacokinetic study rats'],
  ['Pharma', 'nanoparticle_drug', 'drug loaded nanoparticle encapsulation efficiency release'],
  ['Pharma', 'solubility_enhancement', 'solid dispersion solubility enhancement poorly soluble drug'],
  // ---- Biology ----
  ['Biology', 'enzyme_kinetics', 'enzyme kinetics Michaelis Menten Km Vmax determination'],
  ['Biology', 'fermentation', 'fermentation yield productivity bioreactor batch culture'],
  ['Biology', 'cell_growth', 'cell growth kinetics Monod model substrate limitation'],
  ['Biology', 'protein_expression', 'recombinant protein expression yield E coli optimization'],
  ['Biology', 'biosensor', 'biosensor detection limit sensitivity experimental'],
  // ---- Electrochemistry ----
  ['Electrochemistry', 'oer_catalyst', 'oxygen evolution reaction overpotential catalyst electrochemical'],
  ['Electrochemistry', 'her_catalyst', 'hydrogen evolution reaction Tafel slope overpotential catalyst'],
  ['Electrochemistry', 'corrosion_polarization', 'potentiodynamic polarization corrosion current density'],
  ['Electrochemistry', 'eis', 'electrochemical impedance spectroscopy charge transfer resistance'],
  ['Electrochemistry', 'cv_capacitance', 'cyclic voltammetry specific capacitance electrode material'],
  // ---- Analysis ----
  ['Analysis', 'hplc', 'HPLC method validation linearity recovery precision'],
  ['Analysis', 'gc_ms', 'GC-MS quantification detection limit validation'],
  ['Analysis', 'xrd_rietveld', 'XRD Rietveld refinement phase quantification'],
  ['Analysis', 'ftir', 'FTIR spectroscopy quantitative analysis calibration'],
  ['Analysis', 'icp_ms', 'ICP-MS trace element determination detection limit'],
  // ---- PDE / Numerics ----
  ['PDE', 'heat_equation', 'finite difference heat equation numerical solution benchmark'],
  ['PDE', 'navier_stokes_numeric', 'numerical solution Navier Stokes benchmark accuracy'],
  ['PDE', 'fem_convergence', 'finite element method convergence rate benchmark problem'],
  ['PDE', 'diffusion_reaction', 'reaction diffusion equation numerical simulation validation'],
  ['PDE', 'wave_equation', 'wave equation numerical scheme stability accuracy']
];

async function search(query, rows = 3) {
  const u = new URL('https://api.crossref.org/works');
  u.searchParams.set('query.bibliographic', query);
  u.searchParams.set('rows', String(rows));
  u.searchParams.set('filter', 'type:journal-article,has-abstract:true');
  u.searchParams.set('sort', 'relevance');
  u.searchParams.set('select', 'DOI,title,container-title,published,is-referenced-by-count,author,publisher,type');
  u.searchParams.set('mailto', MAILTO);
  const r = await fetch(u, { headers: { 'User-Agent': `SwarmLabs-Validator/1.0 (mailto:${MAILTO})` } });
  if (!r.ok) throw new Error('http ' + r.status);
  const j = await r.json();
  return (j.message && j.message.items) || [];
}

(async () => {
  const out = [];
  const seen = new Set();
  let i = 0;
  for (const [domain, topic, query] of TOPICS) {
    i++;
    try {
      const items = await search(query, 3);
      // 同主题取引用量最高的一篇，保证权威性
      const best = items
        .filter(x => x.DOI && !seen.has(x.DOI.toLowerCase()))
        .sort((a, b) => (b['is-referenced-by-count'] || 0) - (a['is-referenced-by-count'] || 0))[0];
      if (!best) { console.log(`- [${domain}] ${topic}: 无结果`); continue; }
      seen.add(best.DOI.toLowerCase());
      const rec = {
        domain,
        topic,
        doi: best.DOI.toLowerCase(),
        title: Array.isArray(best.title) ? best.title[0] : best.title,
        journal: Array.isArray(best['container-title']) ? best['container-title'][0] : best['container-title'],
        year: (best.published && best.published['date-parts'] && best.published['date-parts'][0][0]) || null,
        citations: best['is-referenced-by-count'] || 0,
        first_author: best.author && best.author[0] ? [best.author[0].family, best.author[0].given].filter(Boolean).join(', ') : null,
        source: 'CrossRef',
        value_extracted: false,
        note: '文献引用池条目：DOI 与元数据经 CrossRef 校验为真实，数值尚未从原文抽取，不计入已比对基准。'
      };
      out.push(rec);
      console.log(`✓ [${domain}] ${topic} (${i}/${TOPICS.length}) cит=${rec.citations} ${String(rec.journal || '').slice(0, 34)} ${rec.year}`);
    } catch (e) {
      console.log(`✗ [${domain}] ${topic}: ${e.message}`);
    }
    await new Promise(s => setTimeout(s, 260));
  }

  const byDomain = {};
  out.forEach(r => { byDomain[r.domain] = (byDomain[r.domain] || 0) + 1; });

  fs.writeFileSync(OUT, JSON.stringify({
    meta: {
      generated_at: new Date().toISOString(),
      source: 'CrossRef REST API (api.crossref.org)',
      integrity: '所有 DOI 均由 CrossRef 索引返回，非人工填写；数值未抽取，不得作为通过率统计口径。'
    },
    stats: { total: out.length, by_domain: byDomain },
    references: out
  }, null, 2));
  console.log(`\n采集完成: ${out.length} 篇真实文献，按域分布:`, JSON.stringify(byDomain));
  console.log('写入:', OUT);
})();
