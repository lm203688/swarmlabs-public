"""Tool Ocean — capability index for agent discovery.

背景（2026-10-03 实测）
----------------------
`engine/` 下有 76 个模块，但只有 29 个接入 `engine/__init__.py`（截至
2026-09-29 扩展后为 39 个）。**未接入的模块对 Agent 等于隐身**——Agent
经统一入口 `engine.X` 检索不到，只能靠人知道模块名才能 import 到。

这不是"装饰性"问题。SwarmLabs 卖的是"AI4S 可信验证层"，Agent 如果
发现不了 `engine/anchor_independence.py` 的锚点独立性分析、
`engine/discipline_registry.py` 的专业接入脚手架，这些能力等于不存在。

借鉴
----
STELLA（bioRxiv 2025.07.01）的 **Tool Ocean**：Agent 自主发现并扩充
工具集，而不是被告知工具清单。Harvard 的 ToolUniverse
（github.com/Harvard-HMS-ML/ToolUniverse）把 1000+ 科学工具封装成
MCP，用两阶段缓存压缩上下文 99%——核心思路同样是"让工具可发现"。

本模块做的是**最小可行版**：不引入外部依赖，纯 stdlib 扫描 `engine/`
的 AST，生成一份结构化的 capability manifest：每个模块是什么、能干什么、
有哪些公开 API、是否接入统一入口、属于哪个能力域。

不做
----
- 不 import 所有模块（会触发重依赖、文件 IO、甚至副作用）
- 不做运行时语义分析（纯静态 AST，零执行）
- 不自动改 `__init__.py`（那是有风险的改动，应由人显式决定）

信任模型
--------
manifest 本身也是**可被审计的产物**：它记录了"哪些能力对 Agent 可见"，
因此"不可见能力占比"这个数字本身就是诚实性指标——和 SwarmLabs 其余
部分的口径一致（可见性缺口要公开，不是藏起来）。
"""

from __future__ import annotations

import ast
import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

_SCHEMA = "svp.tool-ocean.v1"

#: 能力域 → 模块名前缀/关键词的映射。用于把 76 个模块归到 8 个能力域，
#: 让 Agent 能按"我要做什么"检索，而不是按"模块叫什么"检索。
CAPABILITY_DOMAINS: Dict[str, Tuple[str, ...]] = {
    "surrogate": (
        "surrogate", "emukit", "neural_operator", "fourier_operator", "pinn",
        "acquisition_volume", "baybe_adapter", "random_forest_baseline",
        "small_model", "multifidelity", "world_model", "remote_lab",
    ),
    "verification": (
        "validation", "verification_gate", "verifier_gate", "conformal",
        "conformal_vv", "formal_invariants", "harness_registry",
        "bidirectional_parity", "second_law", "moment_bound", "guard",
        "calibration_rl", "promote_verifier",
        # scitrust_eval: ORNL SciTrust 2.0 四维信任自评（truthfulness /
        # adversarial robustness / scientific safety / scientific ethics），
        # 是**对 V&V 引擎自身**的可信度评估，故归 verification 域。
        "scitrust_eval",
    ),
    "evidence": (
        "wet_lab_anchors", "anchor_intake", "anchor_independence",
        "synthetic_anchors", "scenario_trust_labels", "traceability",
        "real_datasets",
        # tensor_binding: CycloneDX ML-BOM 风格 tensor↔(sample_id, λ) 密码学绑定
        "tensor_binding",
    ),
    "evolution": (
        "harness_evolver", "flywheel", "autonomous_loop", "antirepeat",
        "template_library", "meta_learner", "discovery", "elo",
        "hypothesis_llm", "tree_search",
    ),
    "scenario": (
        "scenarios_extra", "scenarios_science", "molecular_scenarios",
        "scenario_router", "design", "domains", "discipline_registry",
        "microbiology_expanded", "microbiology_full", "strain_registry",
        "media_database", "instrument_simulator", "paper_validation",
        "real_datasets",
        # scenario_browser: 结构化筛选接口（按学科/难度/数据源/锚点状态等）
        "scenario_browser",
    ),
    "data": (
        "entity_shard", "validation_db", "report_generator", "vv_package",
        "vlab_delivery", "vlab_contract", "vlab_stdlib", "vlab_batch",
        "vlab_orchestrator", "vlab_server", "serve",
    ),
    "platform": (
        "sdk", "time_machine", "federated_evidence", "validation_mesh",
        "validation_body", "snapshot", "brand", "demo_full_loop",
    ),
    "ops": (
        "paper_audit", "paper_validation_loop_ops", "unified_ops",
    ),
}


@dataclass
class Capability:
    """一个模块对外暴露的能力描述。"""

    module: str
    docstring: str
    public_classes: List[str] = field(default_factory=list)
    public_functions: List[str] = field(default_factory=list)
    domain: str = "uncategorized"
    wired: bool = False          # 是否接入 engine/__init__.py（对 Agent 可见）
    loc: int = 0
    has_dataclass: bool = False
    parse_error: Optional[str] = None

    @property
    def api_count(self) -> int:
        return len(self.public_classes) + len(self.public_functions)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["api_count"] = self.api_count
        return d


def _first_line(doc: str) -> str:
    if not doc:
        return ""
    for line in doc.strip().splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def classify(module: str) -> str:
    """把模块归到能力域。取最长匹配前缀，避免 ``validation`` 抢走
    ``validation_db`` 的归属（后者应归 data 域）。

    Args:
        module: 模块名（不含 .py）

    Returns:
        能力域名，未命中则 "uncategorized"
    """
    best_domain = "uncategorized"
    best_len = 0
    for domain, keys in CAPABILITY_DOMAINS.items():
        for k in keys:
            if module == k or module.startswith(k + "_"):
                if len(k) > best_len:
                    best_len = len(k)
                    best_domain = domain
            elif k in module and len(k) > best_len:
                best_len = len(k)
                best_domain = domain
    return best_domain


def scan_engine(engine_dir: str) -> List[Capability]:
    """扫描 engine/ 下所有模块，生成 capability 列表（纯 AST，零执行）。

    Args:
        engine_dir: engine 包目录路径

    Returns:
        Capability 列表，按模块名排序
    """
    caps: List[Capability] = []
    if not os.path.isdir(engine_dir):
        return caps
    for fn in sorted(os.listdir(engine_dir)):
        if not fn.endswith(".py"):
            continue
        name = fn[:-3]
        if name == "__init__":
            continue
        path = os.path.join(engine_dir, fn)
        try:
            with open(path, encoding="utf-8") as f:
                src = f.read()
            tree = ast.parse(src)
        except (OSError, SyntaxError, UnicodeDecodeError) as e:
            caps.append(Capability(
                module=name, docstring="", domain=classify(name),
                parse_error=f"{type(e).__name__}: {e}",
            ))
            continue
        classes: List[str] = []
        funcs: List[str] = []
        has_dc = False
        for n in tree.body:
            if isinstance(n, ast.ClassDef):
                if n.name.startswith("_"):
                    continue
                classes.append(n.name)
                for dec in n.decorator_list:
                    tgt = dec.func if isinstance(dec, ast.Call) else dec
                    if isinstance(tgt, ast.Name) and tgt.id == "dataclass":
                        has_dc = True
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if n.name.startswith("_"):
                    continue
                funcs.append(n.name)
        caps.append(Capability(
            module=name,
            docstring=_first_line(ast.get_docstring(tree) or ""),
            public_classes=classes,
            public_functions=funcs,
            domain=classify(name),
            loc=src.count("\n") + 1,
            has_dataclass=has_dc,
        ))
    return caps


def wired_modules(init_path: str) -> set:
    """解析 engine/__init__.py，取出已接入统一入口的模块名集合。

    Args:
        init_path: engine/__init__.py 路径

    Returns:
        模块名集合；文件不存在时返回空集
    """
    wired: set = set()
    if not os.path.isfile(init_path):
        return wired
    try:
        with open(init_path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError, UnicodeDecodeError):
        return wired
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            if n.module:
                wired.add(n.module.split(".")[0])
            else:
                for a in n.names:
                    wired.add(a.name.split(".")[0])
        elif isinstance(n, ast.Import):
            for a in n.names:
                wired.add(a.name.split(".")[0])
    return wired


def find(
    caps: List[Any], query: str, limit: int = 10
) -> List[Capability]:
    """按关键词检索能力。纯字符串匹配，无向量依赖（零依赖原则）。

    匹配优先级：模块名精确 > docstring 命中 > API 名命中 > 域名命中。
    这让"我知道模块名"和"我只记得它做什么"两种检索方式都成立。

    Args:
        caps: capability 列表。可以是 ``Capability`` 对象（来自
            ``scan_engine``）或 manifest 里的 dict（来自
            ``manifest["capabilities"]``）——两种都支持，方便在
            「构建期用对象、消费期读 JSON」之间切换。
        query: 查询串（大小写不敏感）
        limit: 最多返回条数

    Returns:
        按相关度降序排列的 capability（保持输入类型）
    """
    q = (query or "").strip().lower()
    if not q:
        return []

    def get(c: Any, k: str) -> str:
        if isinstance(c, dict):
            v = c.get(k, "")
        else:
            v = getattr(c, k, "")
        return str(v or "").lower()

    def apis(c: Any) -> List[str]:
        if isinstance(c, dict):
            return list(c.get("public_classes", [])) + \
                list(c.get("public_functions", []))
        return list(getattr(c, "public_classes", []) or []) + \
            list(getattr(c, "public_functions", []) or [])

    scored: List[Tuple[int, Any]] = []
    for c in caps:
        score = 0
        mod = get(c, "module")
        if mod == q:
            score += 100
        elif q in mod:
            score += 40
        if q in get(c, "docstring"):
            score += 25
        for api in apis(c):
            if q in str(api).lower():
                score += 10
        if get(c, "domain") == q:
            score += 15
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda x: (-x[0], str(get(x[1], "module"))))
    return [c for _, c in scored[:limit]]


def build_manifest(engine_dir: str) -> Dict[str, Any]:
    """生成 capability manifest。

    Args:
        engine_dir: engine 包目录

    Returns:
        manifest dict（含 overall 统计 + capabilities 全量 + 按域分组）
    """
    caps = scan_engine(engine_dir)
    wired = wired_modules(os.path.join(engine_dir, "__init__.py"))
    for c in caps:
        c.wired = c.module in wired

    by_domain: Dict[str, List[str]] = {}
    for c in caps:
        by_domain.setdefault(c.domain, []).append(c.module)

    visible = [c for c in caps if c.wired]
    hidden = [c for c in caps if not c.wired]
    total_api = sum(c.api_count for c in caps)
    visible_api = sum(c.api_count for c in visible)

    return {
        "schema": _SCHEMA,
        "generated_at": int(time.time()),
        "engine_dir": os.path.abspath(engine_dir),
        "overall": {
            "total_modules": len(caps),
            "wired_modules": len(visible),
            "hidden_modules": len(hidden),
            # 可见性缺口：这是诚实性指标，必须公开而非隐藏
            "hidden_pct": round(len(hidden) / max(len(caps), 1) * 100, 1),
            "total_api": total_api,
            "visible_api": visible_api,
            "visible_api_pct": round(visible_api / max(total_api, 1) * 100, 1),
            "parse_errors": [c.module for c in caps if c.parse_error],
            "total_loc": sum(c.loc for c in caps),
        },
        "by_domain": {k: sorted(v) for k, v in sorted(by_domain.items())},
        "capabilities": [c.to_dict() for c in caps],
    }


def save(manifest: Dict[str, Any], path: str) -> str:
    """把 manifest 写到磁盘，返回绝对路径。"""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    return os.path.abspath(path)


def default_manifest_path(repo_root: Optional[str] = None) -> str:
    """默认 manifest 落盘位置：data/capability_manifest.json。"""
    if repo_root is None:
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(repo_root, "data", "capability_manifest.json")


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _selftest() -> bool:
    """纯 stdlib self-test，不依赖外部数据。"""
    passed = 0
    total = 0

    def check(name: str, cond: bool) -> None:
        nonlocal passed, total
        total += 1
        if cond:
            passed += 1
            print(f"  PASS  {name}")
        else:
            print(f"  FAIL  {name}")

    engine_dir = os.path.dirname(os.path.abspath(__file__))
    manifest = build_manifest(engine_dir)
    caps = manifest["capabilities"]
    ov = manifest["overall"]

    # 1. 扫到模块
    check("scans >60 modules", ov["total_modules"] > 60)
    check("no parse errors", not ov["parse_errors"])
    check("has wired modules", ov["wired_modules"] > 20)
    check("hidden_pct in range", 0 <= ov["hidden_pct"] <= 100)
    check("visible_api <= total_api",
          ov["visible_api"] <= ov["total_api"])

    # 2. 分类正确
    check("classify surrogate.py -> surrogate",
          classify("surrogate") == "surrogate")
    check("classify validation_db.py -> data (longest match)",
          classify("validation_db") == "data")
    check("classify wet_lab_anchors.py -> evidence",
          classify("wet_lab_anchors") == "evidence")
    check("classify unknown -> uncategorized",
          classify("zzz_not_a_real_module") == "uncategorized")

    # 3. 核心模块确实 wired
    wired_set = {c["module"] for c in caps if c["wired"]}
    for m in ("surrogate", "formal_invariants", "verification_gate",
              "conformal_vv", "template_library", "wet_lab_anchors"):
        check(f"{m} is wired", m in wired_set)

    # 4. 检索（用对象列表测 find 的对象路径 + dict 列表测 JSON 路径）
    obj_caps = scan_engine(engine_dir)
    r = find(obj_caps, "surrogate", limit=5)
    check("find returns results", len(r) > 0)
    check("find by module name ranks that module first",
          bool(r) and r[0].module == "surrogate")
    r2 = find(caps, "conformal", limit=5)
    check("find by keyword finds conformal modules",
          any("conformal" in str(c.get("module", c)) for c in r2))
    check("find on dicts works (JSON path)",
          bool(find(caps, "anchor")) and
          isinstance(find(caps, "anchor", limit=1)[0], dict))
    check("find empty query returns []", find(caps, "") == [])
    check("find nonsense query returns []", find(caps, "zzz_qqq") == [])

    # 5. by_domain 完整性
    check("by_domain covers all modules",
          sum(len(v) for v in manifest["by_domain"].values())
          == ov["total_modules"])

    # 6. save/load roundtrip
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp = f.name
    try:
        p = save(manifest, tmp)
        check("save returns abs path", os.path.isabs(p))
        with open(p, encoding="utf-8") as f2:
            back = json.load(f2)
        check("manifest roundtrip", back["overall"] == ov)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    # 7. 失败路径
    check("scan_engine on bad dir returns []",
          scan_engine("/nonexistent/dir") == [])
    check("wired_modules on bad file returns set()",
          wired_modules("/nonexistent/file.py") == set())

    print(f"\n{passed}/{total} tool-ocean self-tests passed")
    return passed == total


if __name__ == "__main__":
    import sys
    if _selftest():
        # 自测通过后落盘 manifest（供 Agent / CI 消费）
        eng = os.path.dirname(os.path.abspath(__file__))
        m = build_manifest(eng)
        out = save(m, default_manifest_path())
        ov = m["overall"]
        print(f"\nmanifest → {out}")
        print(f"  modules: {ov['total_modules']}  "
              f"wired: {ov['wired_modules']}  hidden: {ov['hidden_modules']} "
              f"({ov['hidden_pct']}%)")
        print(f"  api: {ov['visible_api']}/{ov['total_api']} visible "
              f"({ov['visible_api_pct']}%)")
        print(f"  loc: {ov['total_loc']}")
        for d, ms in m["by_domain"].items():
            n_wired = sum(1 for c in m["capabilities"]
                          if c["domain"] == d and c["wired"])
            print(f"  {d:<12} {len(ms):>2} modules ({n_wired} wired)")
        sys.exit(0)
    sys.exit(1)
