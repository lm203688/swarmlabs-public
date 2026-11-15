"""Fail-closed CI gate: engine/scenario_registry.py 契约断言（P1-5）。

为什么这个门禁存在（2026-10-09）
--------------------------------
SwarmLabs 曾经有**四条独立的场景装配路径**（`paper_validation_loop` /
`report_generator` / `scenario_library_density` / `verify_multifidelity_integration`），
每条各自拼一份清单。同一仓库对外可以给出不同的"场景总数"，新增场景时
忘改一边就静默漂移——工作记忆里 2026-10-05 的「13 个新场景凭空消失」
事故就是这个模式。

`engine/scenario_registry.py`（2026-10-08 建立）把装配收敛为单一真源，
但**注册表本身不是自动强制的**——如果有人在别的路径里新增了自己的装配，
或者把某个来源悄悄删除，注册表不会自动察觉，因为它不知道「应该有几
条装配路径」。本脚本把这个契约前移到 CI 硬门禁：

1. **契约完整性**：registry 装配出的场景数必须等于 HEADLINE_SCENARIO_COUNT
   （`95`），或**仅**由 optional 来源缺失（RDKit 缺）解释 → 记为
   DEGRADED 不失败。其他任何缺口都是真漂移，fail-closed。
2. **装配路径归一**：`paper_validation_loop._build_science_scenarios`
   的原始装配必须委派给 `scenario_registry.science_adapters_raw`——
   不允许 PVL 自己再写一份 4 个 try-import 块。此断言用 AST 静态分析，
   不 import PVL（避免 Actions 缺 rdkit 时假绿）。
3. **数字对外一致**：`HEADLINE_SCENARIO_COUNT` 必须与
   `data/public_facts.json#validated_scenarios` 一致。改数字必须同时
   改 registry、check_honesty_invariants.py 与 public_facts 构建脚本
   —— 三处不一致就是漂移。
4. **语义降级地板**（2026-10-15 板块04 P0 补）：按 key 前缀分桶，
   各子集不得低于 `COMPOSITION_FLOOR`。捕获"总数不变但内容缩水"——
   分子场景曾从 20 缩到 2（RDKit 缺失时静默降级），registry 总数字
   未变、契约检查判 PASS，但组成已经塌了。此检查逐桶验下限，与
   check_registry_contract 互补。

用法
----
    python scripts/check_scenario_registry.py           # 文本模式
    python scripts/check_scenario_registry.py --json    # JSON 模式

退出码
------
    0  契约完整、装配路径归一、数字一致、组成不破地板
    1  任何一项失败（MISMATCH / DRIFT / DUPLICATE / DUPLICATED-ASSEMBLY
       / FACTS-DISAGREEMENT / COMPOSITION-SHRINK）

已接入 CI（`.github/workflows/honesty-gate.yml` 第 10 步）——**必须**
通过才允许合并。任何想"关掉这个检查"的 PR 请先读
`engine/scenario_registry.py` 的 docstring 与工作记忆里的
「新增场景必须同时改两处」铁律。
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "engine" / "scenario_registry.py"
PVL_PATH = ROOT / "scripts" / "paper_validation_loop.py"
FACTS_PATH = ROOT / "data" / "public_facts.json"

# 让 engine.* 可导入（Actions / 任意目录直接跑 python scripts/check_scenario_registry.py）
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ===========================================================================
# 装配路径归一：PVL 不允许自己再装 4 个来源
# ===========================================================================
def check_pvl_assembly_not_duplicated() -> list[dict]:
    """静态断言：paper_validation_loop._build_science_scenarios 里
    **不允许**再出现 `from engine.scenarios_(extra|science|extended)`
    或 `from engine.molecular_scenarios` 的直接 import。

    历史：2026-10-08 之前 PVL 里这段函数自己写 4 个 try-import 块，
    与 registry 里的 4 个适配器逐字重复。2026-10-09 已改为委派给
    `scenario_registry.science_adapters_raw`。本检查防止回归。

    为什么用 AST 而不是 grep：避免匹配到注释里的历史描述（那些是
    说明"这里曾经怎么装配的"，不是活代码）。AST 只扫 ImportFrom，
    注释和 docstring 天然被排除。

    为什么不用「import PVL 然后看运行时是否走 registry」：Actions
    缺 rdkit 会让 `from engine.molecular_scenarios` 静默降级，
    那条路径的运行时行为会退化，检查会假绿。AST 是纯源码扫描，
    与 Actions 环境无关。
    """
    findings: list[dict] = []
    if not PVL_PATH.exists():
        return [{"kind": "MISSING", "path": str(PVL_PATH),
                 "why": "paper_validation_loop.py 不存在"}]

    src = PVL_PATH.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [{"kind": "SYNTAX", "path": str(PVL_PATH), "why": str(exc)}]

    # 找 _build_science_scenarios 函数
    target_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_build_science_scenarios":
            target_fn = node
            break
    if target_fn is None:
        return [{"kind": "MISSING-FUNC", "path": str(PVL_PATH),
                 "why": "找不到 _build_science_scenarios"}]

    # 收集函数体内的所有 ImportFrom 的 module
    forbidden_modules = {
        "engine.scenarios_extra",
        "engine.scenarios_science",
        "engine.scenarios_extended",
        "engine.molecular_scenarios",
    }
    allowed_delegation = "engine.scenario_registry"

    seen_delegation = False
    bad_imports: list[dict] = []
    for node in ast.walk(target_fn):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        mod = node.module
        if mod == allowed_delegation:
            # 只允许委派给 science_adapters_raw
            names = [a.name for a in node.names]
            if "science_adapters_raw" in names:
                seen_delegation = True
        elif mod in forbidden_modules:
            bad_imports.append({
                "kind": "DUPLICATED-ASSEMBLY",
                "path": str(PVL_PATH),
                "line": node.lineno,
                "module": mod,
                "names": [a.name for a in node.names],
                "why": (
                    f"_build_science_scenarios 里直接 import {mod} —— 装配路径必须"
                    f"委派给 scenario_registry.science_adapters_raw，不允许重复。"
                    f"新增/删除来源时忘改一边就会漂移。"
                ),
            })

    if not seen_delegation:
        bad_imports.insert(0, {
            "kind": "MISSING-DELEGATION",
            "path": str(PVL_PATH),
            "why": (
                "_build_science_scenarios 未调用 scenario_registry.science_adapters_raw —— "
                "装配路径未归一。请检查是否误删了委派调用。"
            ),
        })
    return bad_imports


# ===========================================================================
# 契约完整性：registry 装配出的场景必须等于头条数
# ===========================================================================
def check_registry_contract() -> list[dict]:
    """动态装配 registry 并跑 assert_contract。

    为什么允许降级：无 RDKit 环境下 molecular 来源贡献 0，registry
    会诚实地返回 93 场景（而非 95）。这不是漂移，是环境降级——
    registry.assert_contract 会区分这两种情况（gap 恰好等于
    optional 来源应有数 → degraded=True 不失败；其他情况 → DRIFT）。

    这里把 registry 的 AssertionError 转成 finding，方便与 JSON 模式
    和 CI 输出对齐。
    """
    try:
        from engine.scenario_registry import (  # noqa: WPS433
            HEADLINE_SCENARIO_COUNT,
            load_registry,
        )
    except Exception as exc:  # noqa: BLE001
        return [{"kind": "IMPORT-FAIL", "module": "engine.scenario_registry",
                 "why": f"{type(exc).__name__}: {exc}"}]

    findings: list[dict] = []
    try:
        reg = load_registry()
    except Exception as exc:  # noqa: BLE001
        return [{"kind": "LOAD-FAIL", "why": f"{type(exc).__name__}: {exc}"}]

    try:
        verdict = reg.assert_contract()
    except AssertionError as exc:
        findings.append({
            "kind": "DRIFT",
            "headline": HEADLINE_SCENARIO_COUNT,
            "actual": len(reg),
            "why": str(exc),
        })
        return findings

    if verdict.get("degraded"):
        # 诚实降级：记录但不失败
        findings.append({
            "kind": "DEGRADED",
            "headline": HEADLINE_SCENARIO_COUNT,
            "actual": len(reg),
            "optional_missing": verdict.get("optional_missing"),
            "why": (
                "缺口由可选依赖缺失解释（RDKit 缺失导致 molecular 来源贡献 0）—— "
                "诚实降级，不判失败。"
            ),
        })
    return findings


# ===========================================================================
# 数字对外一致：HEADLINE 必须与 public_facts 对齐
# ===========================================================================
def check_headline_matches_facts() -> list[dict]:
    findings: list[dict] = []
    try:
        from engine.scenario_registry import HEADLINE_SCENARIO_COUNT
    except Exception as exc:  # noqa: BLE001
        return [{"kind": "IMPORT-FAIL", "module": "engine.scenario_registry",
                 "why": str(exc)}]

    if not FACTS_PATH.exists():
        return [{"kind": "MISSING", "path": str(FACTS_PATH),
                 "why": "public_facts.json 缺失"}]

    try:
        _doc = json.loads(FACTS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [{"kind": "JSON", "path": str(FACTS_PATH), "why": str(exc)}]
    # public_facts.json 的顶层是 {name, generated_at_utc, sources, facts, ...}；
    # 事实字典在 doc["facts"] 下（这是 build_public_facts.py 的既有约定）。
    facts = _doc.get("facts") or {}

    validated = facts.get("validated_scenarios", {}).get("value")
    if validated is None:
        return [{"kind": "MISSING-KEY", "path": str(FACTS_PATH),
                 "why": "public_facts.json 缺 validated_scenarios"}]

    if validated != HEADLINE_SCENARIO_COUNT:
        findings.append({
            "kind": "FACTS-DISAGREEMENT",
            "headline": HEADLINE_SCENARIO_COUNT,
            "facts_validated_scenarios": validated,
            "why": (
                f"scenario_registry.HEADLINE_SCENARIO_COUNT={HEADLINE_SCENARIO_COUNT} "
                f"与 public_facts.json#validated_scenarios={validated} 不一致 —— "
                f"改数字必须同时改 registry 与 public_facts 构建脚本。"
            ),
        })
    return findings


# ===========================================================================
# 语义降级守护：组成分布不允许整体不变而子集缩水
# ===========================================================================
#: 板块 04 P0 修复（2026-10-15）——"总数不变但内容缩水"事件
#:
#: 历史：molecular 子集曾从 20 缩到 2（RDKit 缺失时静默降级），
#: 但 registry 总数字未变，check_registry_contract 判 PASS。
#: 这是"数字门禁防不住语义降级"的经典案例。
#:
#: 硬地板（COMPOSITION_FLOOR）—— 每个子集低于此数即视为内容缩水：
#:   - surrogate：至少 2 个（Brannin/Hartmann 是优化算法压测基线）
#:   - micro：    至少 20（微生物工艺是产品定位核心）
#:   - mol：      至少 1 （RDKit 缺失时可到 0，但 registry 应主动降级）
#:   - other：    至少 15（跨学科覆盖是 AI4S 可信验证层的立论根基）
#:
#: 判定方式：按 key 前缀分桶。前缀规则与 scenario_trust_labels.py 一致：
#:   - surrogate/pde 前缀 → surrogate
#:   - mol_ 前缀 → mol
#:   - micro_ 前缀 → micro
#:   - 其他 → other
COMPOSITION_FLOOR = {
    "surrogate": 2,
    "micro": 20,
    "mol": 1,
    "other": 15,
}

#: 允许的整体缩水（可选依赖缺失） —— 触发时降为 DEGRADED 而非 FAIL
#: 例：RDKit 缺失 → mol 从 2 掉到 0，但总数从 95 掉到 93（-2）
#: 只要 optional 来源缺席且 gap 恰好等于其应有量，就归为诚实降级。
_OPTIONAL_SOURCE_FLOORS_RELAXED = {
    "mol": 0,  # RDKit 缺失时可接受 0
}


def _bucket_by_prefix(reg) -> dict:
    """按 key 前缀把 registry 分成 4 桶。"""
    buckets = {"surrogate": 0, "micro": 0, "mol": 0, "other": 0}
    for s in reg:
        key = (s.get("key") or "").lower()
        if key.startswith("surrogate_") or key.startswith("pde_"):
            buckets["surrogate"] += 1
        elif key.startswith("mol_"):
            buckets["mol"] += 1
        elif key.startswith("micro_"):
            buckets["micro"] += 1
        else:
            buckets["other"] += 1
    return buckets


def check_composition_semantic_floor() -> list[dict]:
    """语义降级门禁：捕获"总数不变但子集缩水"。

    这是 check_registry_contract 的补集——前者只数总数，本函数逐桶验下限。
    两个门禁合起来才叫"防得住语义降级"。
    """
    findings: list[dict] = []
    try:
        from engine.scenario_registry import load_registry
    except Exception as exc:  # noqa: BLE001
        return [{"kind": "IMPORT-FAIL", "module": "engine.scenario_registry",
                 "why": f"{type(exc).__name__}: {exc}"}]

    try:
        reg = load_registry()
    except Exception as exc:  # noqa: BLE001
        return [{"kind": "LOAD-FAIL", "why": f"{type(exc).__name__}: {exc}"}]

    buckets = _bucket_by_prefix(reg)
    floors = dict(COMPOSITION_FLOOR)

    # 检查是否有 optional 来源缺失（RDKit 场景）
    try:
        from engine.scenario_registry import OPTIONAL_SOURCES
    except Exception:
        OPTIONAL_SOURCES = ()

    # 判断 registry 是否处于 optional 降级状态
    degraded = False
    try:
        verdict = reg.assert_contract()
        degraded = bool(verdict.get("degraded"))
    except Exception:
        degraded = False

    # 若降级且 mol 地板已可放松到 0，则应用放松
    if degraded:
        floors.update(_OPTIONAL_SOURCE_FLOORS_RELAXED)

    shrunk = []
    for bucket, floor in floors.items():
        actual = buckets.get(bucket, 0)
        if actual < floor:
            shrunk.append({"bucket": bucket, "actual": actual, "floor": floor})

    if shrunk:
        kind = "DEGRADED" if (degraded and all(
            s["bucket"] in _OPTIONAL_SOURCE_FLOORS_RELAXED for s in shrunk
        )) else "COMPOSITION-SHRINK"
        findings.append({
            "kind": kind,
            "buckets": buckets,
            "floors": floors,
            "shrunk": shrunk,
            "optional_missing": getattr(reg, "optional_missing", None)
            if hasattr(reg, "optional_missing") else None,
            "why": (
                "至少一个子集跌破语义地板（可能为 RDKit 缺失诚实降级）："
                + "; ".join(
                    f"{s['bucket']} actual={s['actual']} < floor={s['floor']}"
                    for s in shrunk
                )
            ),
        })

    return findings


# ===========================================================================
# CLI
# ===========================================================================
def run_checks() -> list[dict]:
    out: list[dict] = []
    out.extend(check_pvl_assembly_not_duplicated())
    out.extend(check_registry_contract())
    out.extend(check_headline_matches_facts())
    out.extend(check_composition_semantic_floor())
    return out


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    findings = run_checks()

    # DEGRADED 不算失败；其他 kind 都是 fatal
    fatal = [f for f in findings if f["kind"] != "DEGRADED"]

    if as_json:
        print(json.dumps(
            {"ok": not fatal, "findings": findings,
             "fatal_count": len(fatal)},
            indent=2, ensure_ascii=False,
        ))
    else:
        print("=" * 72)
        print("check_scenario_registry")
        print("=" * 72)
        if not findings:
            print("PASS  装配路径归一 + registry 契约完整 + 数字对外一致")
        else:
            for f in findings:
                mark = "OK  " if f["kind"] == "DEGRADED" else "FAIL"
                print(f"  [{mark}] {f['kind']}  {f.get('why', '')}")
                if f.get("headline") is not None:
                    print(f"         headline={f['headline']}"
                          f"  actual={f.get('actual')}"
                          f"  optional_missing={f.get('optional_missing')}")
                if f.get("facts_validated_scenarios") is not None:
                    print(f"         facts_validated_scenarios="
                          f"{f['facts_validated_scenarios']}")
                if f.get("line"):
                    print(f"         line {f['line']}  module={f.get('module')}"
                          f"  names={f.get('names')}")
        print()
        print("SELFTEST", "PASS" if not fatal else "FAIL",
              f"({len(fatal)} fatal, {len(findings)} total)")

    return 1 if fatal else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
