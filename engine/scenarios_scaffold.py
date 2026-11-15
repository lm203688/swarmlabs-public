"""场景库脚手架生成器（Path C scaffolding, 2026-10-22）。

用途
----
SwarmLabs 场景库目前 95 个（10 个规范学科，`scenario_registry.disciplines()` 归并口径），目标 200 个。手写一个新场景要：
  - 写 f(x) 闭式函数（或调用外部求解器）
  - 定 bounds、初始 ls/noise
  - 生成 train/test 采样
  - 加 references
  - 走 scenario_registry 装配
  - 过 honesty-gate

这六步重复度高。本模块提供一个**模板 + CLI**，让开发者：

    python engine/scenarios_scaffold.py --key new_scenario --dim 3 \
        --template separable --discipline chemistry

生成一份**骨架文件**（`engine/scenarios_draft/<key>.py`），填了：
  - 闭式函数占位（`def f(x): ...`）
  - bounds（默认 [-2, 2]^dim）
  - 采样器（默认 Sobol，可切换 random/grid）
  - references 字段
  - `SCENARIO = {...}` 字典（可直接被 registry 消费）

开发者只需填 `f(x)` 本体 + 学科/引用元数据。然后：

    python engine/scenarios_scaffold.py --register engine/scenarios_draft/new_scenario.py

把它注册进 `scenario_registry._SOURCE_ADAPTERS`（本模块提供 diff 建议，不自动改）。

诚实边界
--------
  - 骨架本身**不进** scenario_registry，必须经开发者审阅 + `assert_contract` 通过才装配。
  - `--register` 只输出"该往哪里加 adapter"的 diff 提示，不改 registry 源码。
  - 目标不是"自动生成 200 个场景"，而是"让人批量写场景更快"。200 场景的
    **实数**只能由开发者 + 一手学术引用 + 真实验证共同凑齐。

模板目录
--------
templates/
    separable.py       # f(x) = sum_i g_i(x_i)（大多数 PDE/统计场景）
    smooth_quadratic.py   # f(x) = x^T A x + b^T x + c
    oscillatory.py    # f(x) = prod_i cos(w_i x_i)
    pde_steady.py     # -∇²u + V u = f, 用离散 Laplacian 求解
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import textwrap
from typing import Dict, List, Optional

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRAFT_DIR = os.path.join(ROOT, "engine", "scenarios_draft")


# ---------------------------------------------------------------- templates
TEMPLATES: Dict[str, Dict[str, str]] = {
    "separable": {
        "description": "f(x) = sum_i g_i(x_i). 大多数 PDE、统计模型、独立坐标问题。",
        "code": '''def f(x):
    """Separable objective: sum_i g_i(x_i).

    TODO: 填 g_1, g_2, ... g_dim 的具体表达式。示例：
        return np.sum(x**2)                # Rosenbrock-like
        return np.sum(np.sin(x))           # 振荡型
    """
    return float(np.sum(np.sin(x)))
''',
    },
    "smooth_quadratic": {
        "description": "f(x) = x^T A x + b^T x + c。凸二次。",
        "code": '''def f(x):
    """Smooth quadratic: f(x) = x^T A x + b^T x + c.

    TODO: 填 A (正定矩阵), b, c。
    """
    # A 正定占位：A = I + 0.1 * random_symmetric
    return float(np.dot(x, x) + 0.5 * x[0] - 0.3 * x[1] + 1.0)
''',
    },
    "oscillatory": {
        "description": "f(x) = prod_i cos(w_i x_i)。多模态测试。",
        "code": '''def f(x):
    """Oscillatory: prod_i cos(w_i * x_i). 多模态测试。"""
    w = np.array([1.0, 2.0, 0.5, 3.0, 1.5, 2.5][:len(x)])
    return float(np.prod(np.cos(w * x)))
''',
    },
    "pde_steady": {
        "description": "-∇²u + V(x) u = f(x)。稳态 PDE。",
        "code": '''def f(x):
    """Steady-state PDE: -lap(u) + V(x)*u = source(x).

    用解析解或有限差分近似。示例（1D 简化）:
        u(x) = sin(pi*x) + 0.5*x^2  （构造性的 V 和 source）
    """
    # 占位：解析解
    return float(np.sum(np.sin(np.pi * x)))
''',
    },
}


def _sobol_like_uniform(dim: int, n: int, bounds, seed: int = 0) -> np.ndarray:
    """低分散随机采样（Sobol-like；用 Halton/stratified 的简化替代）。"""
    rng = np.random.default_rng(seed)
    X = np.zeros((n, dim), float)
    for i in range(n):
        for d in range(dim):
            lo, hi = bounds[d]
            X[i, d] = lo + (hi - lo) * rng.random()
    return X


# ---------------------------------------------------------------- 骨架生成
def generate_scenario_file(key: str, dim: int, template: str, discipline: str,
                            bounds: Optional[List[List[float]]] = None,
                            references: Optional[List[str]] = None,
                            published: str = "",
                            seed: int = 0,
                            n_train: int = 24,
                            n_test: int = 48,
                            ) -> str:
    """生成场景骨架文件，返回文件路径。"""
    if template not in TEMPLATES:
        raise KeyError(f"未知模板 {template!r}，可选: {list(TEMPLATES.keys())}")

    if bounds is None:
        bounds = [[-2.0, 2.0] for _ in range(dim)]
    if references is None:
        references = ["TODO: 添加至少一条学术引用"]

    os.makedirs(DRAFT_DIR, exist_ok=True)
    path = os.path.join(DRAFT_DIR, f"{key}.py")

    content = f'''"""场景骨架: {key} (Path C scaffolding, 2026-10-22).

模板: {template} — {TEMPLATES[template]["description"]}
学科: {discipline}
维度: {dim}

**开发流程**:
  1. 填 `f(x)` 的具体表达式（当前是模板占位）。
  2. 核对 bounds 是否合理（当前 [-2, 2]^{dim}）。
  3. 补 references（学术引用/来源 URL）。
  4. 本地跑通：
        python -c "import importlib.util as m; \\
                   s = m.spec_from_file_location('{key}', r'{path}'); \\
                   mod = m.module_from_spec(s); s.loader.exec_module(mod); \\
                   print('f(0) =', mod.SCENARIO['f'](np.zeros({dim})))"
  5. 用 `python engine/scenarios_scaffold.py --register {path}` 生成注册建议。
  6. 让开发者审阅并 merge 到 scenarios_*.py，走 honesty-gate。

**不要**手动改这个骨架文件的 SCENARIO 字典结构。
"""
from __future__ import annotations

import numpy as np

DIM = {dim}
BOUNDS = {bounds!r}

{TEMPLATES[template]["code"]}

SCENARIO = {{
    "key": "{key}",
    "f": f,
    "bounds": BOUNDS,
    "dim": DIM,
    "ls": 0.5,
    "noise": 0.03,
    "auto_ls": True,
    "discipline": "{discipline}",
    "references": {references!r},
    "published": "{published}",
    # 采样参数（scaffold 生成；正式装配前请审阅）
    "n_train": {n_train},
    "n_test": {n_test},
    "sampler": "stratified_random",
    "sampler_seed": {seed},
    # 场景元数据
    "generated_by": "engine.scenarios_scaffold.generate_scenario_file",
    "scaffold_version": "1.0.0",
}}


def sample_train_test(seed: int = {seed},
                       n_train: int = {n_train},
                       n_test: int = {n_test}):
    """生成训练/测试采样（stratified random）。"""
    rng = np.random.default_rng(seed)
    X_train = np.array([
        [BOUNDS[d][0] + rng.random() * (BOUNDS[d][1] - BOUNDS[d][0]) for d in range(DIM)]
        for _ in range(n_train)
    ])
    X_test = np.array([
        [BOUNDS[d][0] + rng.random() * (BOUNDS[d][1] - BOUNDS[d][0]) for d in range(DIM)]
        for _ in range(n_test)
    ])
    y_train = np.array([f(x) for x in X_train])
    y_test = np.array([f(x) for x in X_test])
    return X_train, y_train, X_test, y_test
'''

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


# ---------------------------------------------------------------- 注册建议
def register_suggestion(scenario_file: str) -> str:
    """返回把该骨架文件接入 scenario_registry 的 diff 建议（不自动改）。"""
    spec = importlib.util.spec_from_file_location("_scaffold", scenario_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sc = mod.SCENARIO
    return f"""
============================================================
接入 scenario_registry 的建议（需要开发者手动 merge）

在 engine/scenarios_extended.py 的 get_extended_scenarios() 或对应学科
文件的返回 dict 里加入：

    {{
        'key': '{sc['key']}',
        'f': <你的具体 f(x)>
        'bounds': {sc['bounds']},
        'dim': {sc['dim']},
        'ls': {sc['ls']},
        'noise': {sc['noise']},
        'discipline': '{sc['discipline']}',
        'references': {sc['references']},
    }}

同时在 engine/scenario_registry.py 的 HEADLINE_SCENARIO_COUNT 加 1。

然后跑：
    python -m engine.scenario_registry  # 契约断言
    python scripts/check_engine_gate.py  # 门禁自检
    python scripts/check_scenario_registry.py  # 场景漂移检查

**不要**手动改 scenario_registry.py 里的装配函数（_adapt_core 等），
那会破坏 registry 的单一真源设计。走新增 scenarios_draft 目录 +
新增一个 _adapt_draft() 的适配器（放在 scenarios_extended 内）是唯一
合规路径。
"""


# ---------------------------------------------------------------- CLI
def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="SwarmLabs 场景库脚手架生成器")
    sub = p.add_subparsers(dest="cmd", required=True)

    gen = sub.add_parser("gen", help="生成场景骨架文件")
    gen.add_argument("--key", required=True)
    gen.add_argument("--dim", type=int, required=True)
    gen.add_argument("--template", default="separable",
                     choices=list(TEMPLATES.keys()))
    gen.add_argument("--discipline", default="unspecified")
    gen.add_argument("--bounds", nargs="*", help="lo hi 对，逗号分隔")
    gen.add_argument("--references", nargs="*", default=[])
    gen.add_argument("--published", default="")
    gen.add_argument("--seed", type=int, default=0)
    gen.add_argument("--n-train", type=int, default=24)
    gen.add_argument("--n-test", type=int, default=48)

    reg = sub.add_parser("register", help="打印注册建议")
    reg.add_argument("scenario_file")

    args = p.parse_args(argv)

    if args.cmd == "gen":
        bounds = None
        if args.bounds:
            pairs = args.bounds
            if len(pairs) != args.dim * 2:
                print(f"[err] bounds 长度应为 {args.dim * 2}，实际 {len(pairs)}")
                return 2
            bounds = [[float(pairs[i]), float(pairs[i + 1])] for i in range(0, len(pairs), 2)]
        path = generate_scenario_file(
            key=args.key, dim=args.dim, template=args.template,
            discipline=args.discipline, bounds=bounds,
            references=list(args.references) or None,
            published=args.published, seed=args.seed,
            n_train=args.n_train, n_test=args.n_test,
        )
        print(f"OK  骨架已生成: {path}")
        print(f"    模板: {args.template}")
        print(f"    下一步: 填 f(x) → register → 合并到 scenarios_extended")
        return 0

    if args.cmd == "register":
        print(register_suggestion(args.scenario_file))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
