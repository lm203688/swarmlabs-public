// 专业数据库连接器 (SwarmLabs)
// 用途: 从权威专业数据库实时拉取真实数据, 转为平台验证记录, 扩充真实验证数据量。
// 当前沙箱出网受限(仅 swarmlabs.tools 可达), 故本脚本在"有网环境"(本地/CI/平台放开网络后)运行。
// 三个数据源:
//   1) PubChem PUG-REST  —— 免费, 无需 key。化合物分子量/SMILES/物性。
//   2) NIST Chemistry WebBook —— 免费, 无需 key。热化学/谱图/物性(需 HTML 解析部分)。
//   3) Materials Project  —— 需 MP_API_KEY(免费申请 materialsproject.org)。材料晶格/带隙/形成能。
// 用法:
//   MP_API_KEY=xxx node scripts/db_connectors.js --source pubchem --query aspirin,benzene,caffeine
//   node scripts/db_connectors.js --source all
// 输出: data/db-pull-<source>-<date>.json (records 数组, 可直接并入 benchmark-dataset.json)

const fs = require('fs');
const path = require('path');
const DATA = path.join(__dirname, '..', 'data');

const args = process.argv.slice(2);
function arg(name) { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : null; }
const source = arg('--source') || 'pubchem';
const query = arg('--query');

async function getJSON(url) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 20000);
  try {
    const r = await fetch(url, { signal: ctrl.signal, headers: { 'User-Agent': 'SwarmLabs/1.0' } });
    clearTimeout(t);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
  } finally { clearTimeout(t); }
}

// ---- 1) PubChem ----
async function pullPubChem(names) {
  const recs = [];
  for (const name of names) {
    try {
      const j = await getJSON(`https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/${encodeURIComponent(name)}/property/MolecularWeight,IsomericSMILES,ExactMass/JSON`);
      const props = j.PropertyTable.Properties[0];
      recs.push({
        source: 'pubchem', citation: `PubChem CID ${props.CID || 'n/a'} (pubchem.ncbi.nlm.nih.gov)`,
        domain: 'chemistry', engine_type: 'property_lookup',
        experiment_name: `PubChem: ${name}`,
        input_parameters: { compound: name },
        expected_output: { molecular_weight: props.MolecularWeight, exact_mass: props.ExactMass, smiles: props.IsomericSMILES, unit: 'g/mol' },
        benchmark_range: { molecular_weight: { min: props.MolecularWeight * 0.98, max: props.MolecularWeight * 1.02, typical: props.MolecularWeight } },
        dp_method: 'none_real_data', created_at: new Date().toISOString(), version: 'db-v1'
      });
      console.log(`  PubChem OK: ${name} MW=${props.MolecularWeight}`);
    } catch (e) { console.log(`  PubChem FAIL: ${name} (${e.message})`); }
  }
  return recs;
}

// ---- 2) NIST (物性查询, 返回摘要 JSON 端点) ----
async function pullNIST(names) {
  const recs = [];
  for (const name of names) {
    try {
      // NIST 物性(如热容)有名查端点; 这里示例拉取化合物基本信息
      const j = await getJSON(`https://webbook.nist.gov/cgi/cbook.cgi?Name=${encodeURIComponent(name)}&Units=SI&Mask=1&Type=JANAFG&Table=on`);
      recs.push({
        source: 'nist', citation: `NIST Chemistry WebBook: ${name} (webbook.nist.gov)`,
        domain: 'chemistry', engine_type: 'thermochemistry',
        experiment_name: `NIST: ${name}`,
        input_parameters: { compound: name },
        expected_output: { note: 'thermochemistry data fetched', raw_bytes: (j && j.length) || 0 },
        benchmark_range: {}, dp_method: 'none_real_data', created_at: new Date().toISOString(), version: 'db-v1'
      });
      console.log(`  NIST OK: ${name}`);
    } catch (e) { console.log(`  NIST FAIL: ${name} (${e.message})`); }
  }
  return recs;
}

// ---- 3) Materials Project ----
async function pullMaterialsProject(formulas) {
  const key = process.env.MP_API_KEY;
  if (!key) { console.log('  [skip] Materials Project 需要 MP_API_KEY 环境变量'); return []; }
  const recs = [];
  for (const f of formulas) {
    try {
      const j = await getJSON(`https://api.materialsproject.org/materials/core/?formula=${encodeURIComponent(f)}&fields=band_gap,formation_energy_per_atom,energy_above_hull`, {
        headers: { 'X-API-KEY': key }
      });
      const data = (j && j.data) || [];
      for (const d of data.slice(0, 3)) {
        recs.push({
          source: 'materials_project', citation: `Materials Project mp-${d.material_id} (materialsproject.org)`,
          domain: 'materials', engine_type: 'crystal_properties',
          experiment_name: `MP: ${f} (${d.material_id})`,
          input_parameters: { formula: f, material_id: d.material_id },
          expected_output: { band_gap_eV: d.band_gap, formation_energy_per_atom_eV: d.formation_energy_per_atom, energy_above_hull_eV: d.energy_above_hull },
          benchmark_range: { band_gap_eV: { min: (d.band_gap || 0) * 0.8, max: (d.band_gap || 0) * 1.2, typical: d.band_gap } },
          dp_method: 'none_real_data', created_at: new Date().toISOString(), version: 'db-v1'
        });
      }
      console.log(`  MP OK: ${f} (${data.length} hits)`);
    } catch (e) { console.log(`  MP FAIL: ${f} (${e.message})`); }
  }
  return recs;
}

(async () => {
  let recs = [];
  if (source === 'pubchem' || source === 'all') {
    const names = (query || 'aspirin,benzene,caffeine,ibuprofen,paracetamol').split(',');
    console.log('Pulling PubChem:', names.join(', '));
    recs = recs.concat(await pullPubChem(names));
  }
  if (source === 'nist' || source === 'all') {
    const names = (query || 'water,benzene,ethanol').split(',');
    console.log('Pulling NIST:', names.join(', '));
    recs = recs.concat(await pullNIST(names));
  }
  if (source === 'materialsproject' || source === 'all') {
    const formulas = (query || 'SiO2,TiO2,Fe2O3').split(',');
    console.log('Pulling Materials Project:', formulas.join(', '));
    recs = recs.concat(await pullMaterialsProject(formulas));
  }
  const out = path.join(DATA, `db-pull-${source}-${new Date().toISOString().slice(0, 10)}.json`);
  fs.mkdirSync(DATA, { recursive: true });
  fs.writeFileSync(out, JSON.stringify({ source, generated: new Date().toISOString(), count: recs.length, records: recs }, null, 2));
  console.log(`\n写入 ${out} (${recs.length} 条). 可在有网环境运行以拉取真实数据库数据。`);
})();
