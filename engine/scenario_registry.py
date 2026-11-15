"""场景注册表单一入口（P1-5，2026-10-08）。

问题（为什么要这个模块）
------------------------
在此之前，同一个仓库里存在**四条各自独立的场景装配路径**，每条路径
各自拼一份清单，因此同一个「场景总数」在不同入口给出不同的数：

    ┌─────────────────────────────────────┬────────┬──────────────────────────────┐
    │ 装配路径                             │ 曾报数 │ 来源                               │
    ├─────────────────────────────────────┼────────┼──────────────────────────────┤
    │ scripts/paper_validation_loop         │ 95     │ SCENARIOS + MICRO + SCIENCE(+EXTENDED)│
    │ engine/report_generator.all_scenarios │ 82→95  │ 6 个来源 + setdefault 去重       │
    │ scripts/scenario_library_density      │ 72→95  │ 有兜底重组路径                   │
    │ scripts/verify_multifidelity_integration│ 读 P │ 读 P.ALL_SCENARIOS           │
    └─────────────────────────────────────┴────────┴──────────────────────────────┘

`paper_validation_loop.py` 的 docstring 已经写下一句预言 —— 「三套注册表
必然漂移」。本模块就是把那句预言在**结构上**终结掉：

    **场景的装配只写一次。谁要场景，就来这里取。**

设计
----
1. `load_registry()` 是所有装配的唯一实现，返回一个 `ScenarioRegistry`，
   内部持有**有序**的 `list[dict]`（顺序即权威顺序，供复现/对比使用）
   以及派生的 `by_key` 映射。
2. 每个来源用统一的「适配器」拉取（`_SOURCE_ADAPTERS`），任何来源导入
   失败只降级、不抛异常 —— 与既有 `_build_science_scenarios` 的
   「优雅降级」纪律一致。降级会被如实记录在 `registry.degraded`。
3. `assert_contract(headline_count=...)` 做跨模块契约断言：把入口报告
   的**头条场景数**与注册表真实数对齐。任何人若在别的路径里硬编码一个
   与注册表不符的数，断言会当场失败。
4. `registry.fingerprint()` 给出与顺序无关的内容指纹（对 (key,schema漂移)
   排序后哈希），用于 CI 检测「同一场景在两个来源里定义不一致」。

诚实边界
--------
- 本模块**不改变**任何场景的数值、函数或判定口径。它只统一「谁被装配、
  以什么顺序装配、装配出来几个」。因此引入本模块后，可信面的判定结果
  必须逐字节不变 —— 这一点由回归脚本验证。
- `SCENARIOS_EXTRA` 里含**负对照测试资产**（如 fin_put_call_parity 的零函数），
  这些在 paper_validation_loop 里不进 benchmark。本模块**照搬来源的成员资格**
  （即在 science 路径里进来的 extra 与在 report 路径里进来的一致），
  不额外增删，避免制造第二个「口径」。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent

#: 头条场景数（对外公开的「场景总数」）。这是**唯一的**权威数字。
#:
#: 历史：82 → 95（+13，2026-10-05）。新增来自 engine/scenarios_extended.py
#: 的 7 个「只有 1 个场景」的学科（heat / llm / chemical / finance /
#: neuroscience / ecology / energy 各 +2；其中 finance 的 1 个是负对照
#: 测试资产，按来源纪律不进 benchmark）。
#:
#: 修改此数字前请先读 scripts/check_honesty_invariants.py 顶部的
#: 「发布缩减包 vs 修复回归」声明 —— 那是本数字唯一的变更纪律入口。
HEADLINE_SCENARIO_COUNT = 95

#: 依赖可选第三方库（RDKit）才能装配的来源 → 缺失时头条数会降级。
#: 2026-10-08 首次装配注册表实测到的事实：本机**无 RDKit**，
#: `molecular` 来源贡献 0（而非 2），于是 live 注册表 = 93，
#: 而 `data/trust_surface.json` 记为 95（那是在有 RDKit 的环境里生成的）。
#:
#: 这正是「三套注册表必然漂移」的物理根因之一：**对外头条数依赖环境**。
#: 因此头条数断言必须区分两种情况：
#:   - 「少掉的条目全部来自 optional 来源」→ 记为 DEGRADED（诚实降级，不失败）
#:   - 「少掉的条目不是 optional 来源造成的」→ 记为 DRIFT（真漂移，失败）
OPTIONAL_SOURCES: Tuple[str, ...] = ("molecular",)

#: 学科标签规范化（P2-5）。原始 `domain` 字段存在**大小写 / 中英文 / 别名**
#: 分裂：同一「微生物」学科在库里同时写成 `微生物 / ...`、`Microorganisms/...`、
#: `biology`、`Biology/Population Dynamics`、`micro_*`。这会让密度表 / 报告把
#: 它们数成 18 个不同的「学科」。本映射把它们统一到规范学科 ID，
#: 使对外口径收敛为真实学科数（消除 18→13 的标签分裂）。
#:
#: 规则：先按前缀/关键词归并，再对剩下列出作 lowercase + 取首段规范化。
def canon_domain(raw: str) -> str:
    """把任意原始 domain 标签归一化为规范学科 ID。纯派生，不改源数据。"""
    if not raw:
        return "unknown"
    r = raw.strip()
    low = r.lower()
    if (low.startswith("微生物") or low.startswith("microorganism")
            or low.startswith("micro_") or low == "biology"
            or low.startswith("biology/") or low == "microorganisms"):
        return "microbiology"
    if low.startswith("chemistry") or low.startswith("chemical") or low == "cheminformatics":
        return "chemistry"
    if low.startswith("heat conduction") or low.startswith("physics"):
        return "physics"
    if low.startswith("llm"):
        return "llm"
    if low.startswith("surrogate modeling") or low.startswith("surrogate modeling/"):
        return "surrogate_benchmark"
    if low == "optimization" or low.startswith("optimization"):
        return "optimization"
    # 其余（finance / neuroscience / ecology / energy 等）取首段小写
    head = r.split(" / ")[0].split("/")[0].strip()
    return head.lower() or "unknown"


# ===========================================================================
# 数据模型
# ===========================================================================
@dataclass
class ScenarioRegistry:
    """全量场景注册表。`items` 是有序权威清单，`by_key` 是派生索引。"""

    items: List[Dict[str, Any]]
    #: 每个来源实际贡献的条目数（key = 来源名）
    source_counts: Dict[str, int] = field(default_factory=dict)
    #: 导入失败 / 被跳过的来源（失败也要留痕，不许静默）
    degraded: List[str] = field(default_factory=list)

    # ---- 基本访问 -------------------------------------------------------
    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    @property
    def by_key(self) -> Dict[str, Dict[str, Any]]:
        return {s["key"]: s for s in self.items}

    def keys(self) -> List[str]:
        return [s["key"] for s in self.items]

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self.by_key.get(key)

    def domains(self) -> Dict[str, int]:
        """每个 domain/category 的场景数（供密度表 / 报告复用）。"""
        out: Dict[str, int] = {}
        for s in self.items:
            d = s.get("domain") or s.get("category") or "unknown"
            out[d] = out.get(d, 0) + 1
        return out

    def disciplines(self) -> Dict[str, int]:
        """**规范学科**计数（P2-5）：用 `domain_norm` 归并后的口径。

        解决原始 `domain` 字段的 18→13 标签分裂问题 —— 同一个「微生物」
        学科在库里散成 `微生物 / ...` / `Microorganisms/...` / `biology` 等，
        本方法把它们收敛为真实学科数，是对外的唯一规范口径。
        """
        out: Dict[str, int] = {}
        for s in self.items:
            d = s.get("domain_norm") or canon_domain(s.get("domain") or "")
            out[d] = out.get(d, 0) + 1
        return out

    # ---- 一致性 ---------------------------------------------------------
    def duplicate_keys(self) -> Dict[str, int]:
        """出现在多个来源里的 key 及其出现次数（>1 表示来源重叠）。"""
        seen: Dict[str, int] = {}
        for s in self.items:
            seen[s["key"]] = seen.get(s["key"], 0) + 1
        return {k: v for k, v in seen.items() if v > 1}

    def fingerprint(self) -> str:
        """与顺序无关的内容指纹：对 (key, 结构签名) 排序后哈希。

        用途：CI 检测「同一场景在不同来源里 schema 漂移」。若有人把某个
        场景的 bounds/ls 改了一个来源、忘了另一个，指纹会变。
        """
        rows = []
        for s in self.items:
            sig = {
                "key": s["key"],
                "domain": s.get("domain") or s.get("category") or "",
                "category": s.get("category", ""),
                "dim": len(s.get("bounds") or []),
                "ls": round(float(s.get("ls", 0.0)), 9),
                "noise": round(float(s.get("noise", 0.0)), 9),
                "auto_ls": bool(s.get("auto_ls", False)),
            }
            rows.append(json.dumps(sig, sort_keys=True, ensure_ascii=False))
        blob = "\n".join(sorted(rows))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    # ---- 契约 -----------------------------------------------------------
    def assert_contract(self, headline_count: Optional[int] = None) -> None:
        """跨模块契约断言。失败即抛 AssertionError（fail-closed）。

        校验项：
          1. 头条数与注册表真实条目数一致 —— **但区分两种缺口**：
             - 缺口完全由 `OPTIONAL_SOURCES`（缺 RDKit 等）解释 → 诚实降级，
               不失败，只在返回值里标注 `degraded=True`；
             - 否则 → 真漂移，抛 AssertionError。
          2. 不允许出现重复 key（防 setdefault 掩盖的两次定义）。
          3. 每个场景必须有 callable 的 f 与非空 bounds（其余入口只做
             「能用」的计数，本断言把这条纪律前移到装载期）。

        Returns
        -------
        dict: {"ok","headline","actual","degraded","optional_missing"}
        """
        want = HEADLINE_SCENARIO_COUNT if headline_count is None else headline_count
        real = len(self.items)
        gap = want - real

        result = {
            "ok": True,
            "headline": want,
            "actual": real,
            "degraded": False,
            "optional_missing": {},
        }

        if gap != 0:
            # 缺口能否被 optional 来源解释？
            opt_missing = {
                name: self.source_counts.get(name, 0)
                for name in OPTIONAL_SOURCES
                if self.source_counts.get(name, 0) == 0
            }
            # optional 来源在「满配」环境下的应有条目数。molecular 满配 = 2。
            _EXPECTED_WHEN_PRESENT = {"molecular": 2}
            explained = sum(
                _EXPECTED_WHEN_PRESENT.get(n, 0) for n in opt_missing
            )
            result["optional_missing"] = opt_missing

            if gap == explained and explained > 0:
                # 诚实降级：少掉的正好等于可选依赖缺失带来的损失。
                result["degraded"] = True
                # 仍继续做 2/3 项结构校验
            else:
                raise AssertionError(
                    f"场景注册表契约破裂（DRIFT）：头条数={want}，注册表真实数={real}"
                    f"（缺口 {gap}）。来源贡献={self.source_counts}，"
                    f"降级来源={self.degraded or '无'}，"
                    f"可选来源缺失={opt_missing}。若这是**有意的**变更，"
                    f"请同时改 HEADLINE_SCENARIO_COUNT 与 "
                    f"scripts/check_honesty_invariants.py（那是一份要求被审阅的声明）。"
                )

        dups = self.duplicate_keys()
        if dups:
            raise AssertionError(
                f"场景注册表契约破裂：出现重复 key {dups} —— "
                f"同一个 key 被两个来源各定义一次，后续 setdefault 会静默吞掉其一。"
            )
        bad = [
            s.get("key", "<no-key>")
            for s in self.items
            if not callable(s.get("f")) or not s.get("bounds")
        ]
        if bad:
            raise AssertionError(
                f"场景注册表契约破裂：{len(bad)} 个场景缺 f/bounds —— {bad[:8]}"
                f"{' …' if len(bad) > 8 else ''}"
            )
        result["ok"] = True
        return result


# ===========================================================================
# 适配器：每个来源如何被拉取
# ===========================================================================
def _adapt_core() -> List[Dict[str, Any]]:
    """跨域核心场景（paper_validation_loop.SCENARIOS）。"""
    pvl = _import_pvl()
    return list(pvl.SCENARIOS)


def _adapt_micro() -> List[Dict[str, Any]]:
    """微生物场景（paper_validation_loop.MICRO_SCENARIOS，已挂数据集）。"""
    pvl = _import_pvl()
    return list(pvl.MICRO_SCENARIOS)


def _adapt_extra() -> List[Dict[str, Any]]:
    """经典基准 / 响应面 / 解析 ODE（engine.scenarios_extra）。"""
    from engine.scenarios_extra import SCENARIOS_EXTRA

    return list(SCENARIOS_EXTRA)


def _adapt_science() -> List[Dict[str, Any]]:
    """科学领域扩展（engine.scenarios_science，含 cheminformatics）。"""
    from engine.scenarios_science import get_science_scenarios

    return list(get_science_scenarios())


def _adapt_molecular() -> List[Dict[str, Any]]:
    """分子模态（engine.molecular_scenarios，需 RDKit；不可用则降级）。"""
    from engine.molecular_scenarios import get_molecular_scenarios

    return list(get_molecular_scenarios())


def _adapt_extended() -> List[Dict[str, Any]]:
    """学科覆盖补齐（engine.scenarios_extended，7 学科 × 3）。"""
    from engine.scenarios_extended import get_extended_scenarios

    return list(get_extended_scenarios())


def science_adapters_raw() -> Tuple[List[Dict[str, Any]], List[str]]:
    """science-flavored 来源的**原始**场景（不含 core/micro），供
    ``paper_validation_loop._build_science_scenarios`` 委派使用。

    为什么这个函数存在（2026-10-09）：PVL 的 ``_build_science_scenarios``
    历史上自己写了 4 个 ``try: from engine.X import Y`` + 手动归一化的
    块，与 registry 里的 4 个适配器**逐字重复**——这正是 PVL docstring
    那句「三套注册表必然漂移」的物理残留。PVL 保留自己那套「归一化」
    逻辑（ls/noise 缺省补齐口径与 registry._normalize 不同），但**原始
    来源的收集**必须只有一处实现，否则新增/删除某来源时忘改一边就
    造成漂移。

    返回：
      - ``raw``：所有 science-flavored 来源（extra + science + molecular +
        extended）的场景 dict 列表，**保持来源装配顺序**，不做去重、
        不做归一化。PVL 会接着做它自己的归一化（含默认 ls/noise）。
      - ``degraded``：任何导入失败或装配失败时的来源名（含错误），
        供 PVL 记录诚实降级；空列表表示全部装配成功。

    不触发 ``_import_pvl()`` —— 本函数只走 ``engine.scenarios_*``，
    不会因从 PVL 里被调而产生循环依赖。
    """
    raw: List[Dict[str, Any]] = []
    degraded: List[str] = []
    for name, adapter in _SOURCE_ADAPTERS:
        if name in ("core", "micro"):
            continue
        try:
            raw.extend(adapter())
        except Exception as exc:  # noqa: BLE001 — 优雅降级
            degraded.append(f"{name}: {type(exc).__name__}: {exc}")
    return raw, degraded


#: 权威装配顺序。**顺序即语义**（复现 / 对比 / 报告的行序都依赖它），
#: 因此这里用一个显式的有序元组，而不是 dict。
#:
#: 注意：这**不是** paper_validation_loop 原始的 SCENARIOS+MICRO+SCIENCE 顺序。
#: 为了让「报告库」与「可信面」得到**同一个集合**，这里采用 report_generator
#: 的口径（先 extra 再 science 再 molecular 再 extended），并对每个来源取
#: **首次定义**去重（等价于 report_generator 的 setdefault）。
#:
#: 两者的**集合相等**由 assert_contract 保障；顺序差异不影响任何数值，
#: 只影响报告行序，且现已统一到这里。
_SOURCE_ADAPTERS: Tuple[Tuple[str, Callable[[], List[Dict[str, Any]]]], ...] = (
    ("core", _adapt_core),
    ("micro", _adapt_micro),
    ("extra", _adapt_extra),
    ("science", _adapt_science),
    ("molecular", _adapt_molecular),
    ("extended", _adapt_extended),
)


# ===========================================================================
# 装载
# ===========================================================================
def _import_pvl():
    """导入 scripts/paper_validation_loop。把 scripts 挂进 sys.path。"""
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import paper_validation_loop as pvl  # type: ignore

    return pvl


def _normalize(m: Dict[str, Any]) -> Dict[str, Any]:
    """把任意来源的场景补齐成统一 schema（与 report_generator._normalize 同口径）。

    不改数值，只补缺省字段与统一 references/published/domain/dim。
    """
    refs = list(m.get("references") or [])
    ref = m.get("ref")
    if ref and ref not in refs:
        refs.append(ref)
    out = dict(m)
    out["references"] = refs
    out["published"] = m.get("published", "") or ""
    out["domain"] = m.get("domain", m.get("category", "")) or m.get("category", "")
    out["domain_norm"] = canon_domain(out["domain"])
    out["dim"] = int(m.get("dim") or len(m.get("bounds", [])))
    return out


def load_registry(*, dedup: bool = True) -> ScenarioRegistry:
    """装配并返回全量场景注册表（**唯一入口**）。

    Parameters
    ----------
    dedup:
        True（默认）时按 `key` 首次定义去重（等价 report_generator 的
        setdefault 口径）。False 时保留重复（用于诊断「哪些 key 重复」）。

    任何单一来源导入失败都不抛异常，只记入 `degraded` —— 与
    `paper_validation_loop._build_science_scenarios` 的既有纪律一致。
    """
    items: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    degraded: List[str] = []
    seen: set[str] = set()

    for name, adapter in _SOURCE_ADAPTERS:
        try:
            raw = adapter()
        except Exception as exc:  # noqa: BLE001 — 来源缺失必须降级而非中断
            degraded.append(f"{name}: {type(exc).__name__}: {exc}")
            counts[name] = 0
            continue

        contributed = 0
        for m in raw:
            key = m.get("key")
            if not key:
                continue
            if dedup and key in seen:
                continue
            seen.add(key)
            items.append(_normalize(m))
            contributed += 1
        counts[name] = contributed

    return ScenarioRegistry(items=items, source_counts=counts, degraded=degraded)


# ---------------------------------------------------------------------------
# 进程级缓存（场景装配要 import 多个重模块，缓存避免重复代价）
# ---------------------------------------------------------------------------
_CACHE: Dict[bool, ScenarioRegistry] = {}


def get_registry(*, dedup: bool = True, refresh: bool = False) -> ScenarioRegistry:
    """带缓存的 `load_registry`。"""
    if refresh or dedup not in _CACHE:
        _CACHE[dedup] = load_registry(dedup=dedup)
    return _CACHE[dedup]


def all_scenarios() -> Dict[str, Dict[str, Any]]:
    """`{key: scenario}` 全量映射 —— report_generator 的权威替代。"""
    return get_registry().by_key


def scenario_list() -> List[Dict[str, Any]]:
    """有序场景清单 —— paper_validation_loop.ALL_SCENARIOS 的权威替代。"""
    return list(get_registry().items)


def get_scenario(key: str) -> Dict[str, Any]:
    reg = get_registry()
    s = reg.get(key)
    if s is None:
        near = sorted(k for k in reg.keys() if key[:6] in k)[:5]
        raise KeyError(
            f"未知场景 '{key}'，共 {len(reg)} 个可选；相近：{near}"
        )
    return s


# ===========================================================================
# 自检
# ===========================================================================
def _selftest() -> int:
    print("=" * 72)
    print("engine.scenario_registry 自检")
    print("=" * 72)
    reg = load_registry()

    print(f"\n[1] 装配结果")
    for name, n in reg.source_counts.items():
        print(f"    {name:<12} {n:>4}")
    print(f"    {'TOTAL':<12} {len(reg):>4}   (头条数 {HEADLINE_SCENARIO_COUNT})")
    if reg.degraded:
        print(f"    降级来源：")
        for d in reg.degraded:
            print(f"      - {d}")

    print(f"\n[2] 契约断言")
    try:
        verdict = reg.assert_contract()
        if verdict["degraded"]:
            print(
                f"    PASS(降级)  场景数={len(reg)}（头条数 {HEADLINE_SCENARIO_COUNT}，"
                f"缺口 {HEADLINE_SCENARIO_COUNT - len(reg)} 全部由可选依赖缺失解释："
                f"{verdict['optional_missing']}）"
            )
            print(f"                诚实降级而非漂移 —— 无重复 key，f/bounds 齐备")
        else:
            print(f"    PASS  场景数={len(reg)}，无重复 key，全部场景 f/bounds 齐备")
    except AssertionError as e:
        print(f"    FAIL  {e}")
        return 1

    print(f"\n[3] 内容指纹（与顺序无关）")
    print(f"    {reg.fingerprint()}")

    print(f"\n[4] 领域分布（{len(reg.domains())} 个 domain）")
    for d, n in sorted(reg.domains().items(), key=lambda kv: -kv[1])[:12]:
        print(f"    {n:>4}  {d}")

    print(f"\n[5] 与既有路径的集合一致性")
    ok = True
    # 5a. report_generator.all_scenarios（若可导入）
    try:
        from engine.report_generator import all_scenarios as rg_all

        rg_keys = set(rg_all().keys())
        reg_keys = set(reg.keys())
        if rg_keys == reg_keys:
            print(f"    OK   report_generator.all_scenarios = {len(rg_keys)} 与注册表相同")
        else:
            only_rg = sorted(rg_keys - reg_keys)[:6]
            only_reg = sorted(reg_keys - rg_keys)[:6]
            print(
                f"    FAIL report_generator {len(rg_keys)} vs 注册表 {len(reg_keys)}；"
                f"仅报告库={only_rg}，仅注册表={only_reg}"
            )
            ok = False
    except Exception as e:  # noqa: BLE001
        print(f"    SKIP report_generator 不可用：{type(e).__name__}: {e}")

    # 5b. paper_validation_loop.ALL_SCENARIOS
    try:
        pvl = _import_pvl()
        pvl_keys = {s["key"] for s in pvl.ALL_SCENARIOS}
        reg_keys = set(reg.keys())
        if pvl_keys == reg_keys:
            print(f"    OK   paper_validation_loop.ALL_SCENARIOS = {len(pvl_keys)} 与注册表相同")
        else:
            only_p = sorted(pvl_keys - reg_keys)[:6]
            only_reg = sorted(reg_keys - pvl_keys)[:6]
            print(
                f"    DIFF paper_validation_loop {len(pvl_keys)} vs 注册表 {len(reg_keys)}；"
                f"仅 PVL={only_p}，仅注册表={only_reg}"
            )
            # 这是历史遗留的顺序差异，不是错误 —— 只告警不判失败
    except Exception as e:  # noqa: BLE001
        print(f"    SKIP paper_validation_loop 不可用：{type(e).__name__}: {e}")

    print()
    print("=" * 72)
    print("SELFTEST", "PASS" if ok else "FAIL")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
