#!/usr/bin/env python3
"""check_engine_contract.py — engine 统一入口的编译期静态契约校验（CI 硬门禁）。

为什么这个脚本存在
-------------------
engine/__init__.py 是全项目的唯一真源入口：Agent、CLI、SDK 都从它拿符号。
但「能 import 成功」不等于「契约完整」。它有两类长期静默的缺陷：

  (a) __all__ 声明了符号，但模块里从未 import / 从未定义。
      访问即 AttributeError。已实锤一例：__all__ 声明了
      run_all_benchmarks_and_export（定义在 engine/validation_db.py:27），
      但 __init__.py 里从未 import 它 —— 该声明在仓库里悬空了多个版本，
      直到 2026-09-27 才靠一次静态扫描发现。

  (b) import 了但来源模块里根本不存在该符号。
      这种是 ImportError，但只在真正 import engine 时才爆，
      而且被上游脚本的 try/except 或 fallback 逻辑吞掉后，
      表现是「某功能悄悄消失」而不是「构建失败」。

两类都符合同一个模式：**部署/运行期才发现，而本可以在合并前一次报全**。

这正是 DataFlow 的 compile() 与 forward() 分离的价值 —— 在执行前把
key 级与依赖级的错误全部摊开成一份报告，而不是靠运行时逐个踩。

本脚本是它的 engine 对应物：**纯 AST 静态分析，不执行任何 engine 代码**。
这一点是刻意的 —— engine 依赖 numpy / scipy / rdkit，如果校验器自己
import engine，Actions 环境缺任一可选依赖时校验器就会自己失败，
门禁就退化成又一个「假绿」。只读源码、只解析 AST，零环境依赖。

用法
----
    python scripts/check_engine_contract.py
    python scripts/check_engine_contract.py --json      # 机器可读输出
    python scripts/check_engine_contract.py --verbose   # 打印孤儿模块全清单

退出码
------
    0  契约完整
    2  存在阻断级问题（P0 / P1），应在合并前修复
    1  工具自身异常（如源码无法读取）
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "engine"
INIT_FILE = ENGINE_DIR / "__init__.py"

# 模块顶层出现这些调用形态，意味着「被 import 的瞬间就产生了副作用」。
# 与 honesty-gate 的哲学一致：入口模块必须可被安全 import。
SIDE_EFFECT_FUNCS = {
    "main", "sys.exit", "os._exit", "exit", "quit",
    "os.system", "subprocess.call", "subprocess.run",
    "subprocess.Popen", "os.spawnl", "os.spawnv",
    "print",
}


# ────────────────────────────── 工具 ──────────────────────────────

def parse_file(path: pathlib.Path) -> ast.AST | None:
    """解析单个 .py 文件；语法错误返回 None 并由调用方记录。"""
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as e:
        return None


def module_paths() -> dict[str, pathlib.Path | None]:
    """收集 engine 下所有模块名 -> 源文件路径的映射。

    覆盖两种形态：engine/xxx.py 单文件模块，engine/xxx/__init__.py 包。
    包记录其 __init__.py；若包无 __init__.py（命名空间包）则记为 None。
    """
    out: dict[str, pathlib.Path | None] = {}
    if not ENGINE_DIR.is_dir():
        return out
    for p in sorted(ENGINE_DIR.iterdir()):
        if p.name.startswith(".") or p.name.startswith("__"):
            continue
        if p.is_file() and p.suffix == ".py" and p.name != "__init__.py":
            out[p.stem] = p
        elif p.is_dir():
            init = p / "__init__.py"
            out[p.name] = init if init.is_file() else None
    return out


def defined_names(tree: ast.AST) -> set[str]:
    """模块顶层真实定义的符号集合（不含 import 进来的名字）。"""
    names: set[str] = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                names.add(a.asname or a.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.With):
            for item in n.items:
                if item.optional_vars is not None and isinstance(item.optional_vars, ast.Name):
                    names.add(item.optional_vars.id)
    return names


def all_defined_or_imported(tree: ast.AST) -> set[str]:
    """顶层可见的全部名字（定义 + import），用于判断符号是否存在。"""
    return defined_names(tree)

# ────────────────────────────── 检查项 ──────────────────────────────

def check_init_syntax(mods: dict[str, pathlib.Path | None]) -> list[dict[str, Any]]:
    """检查 0：engine 下所有模块必须语法可解析。

    语法错误是最高优先级的信号 —— 一旦存在，其余检查的结果都不可信。
    """
    issues: list[dict[str, Any]] = []
    seen_syntax_fail: set[str] = set()
    candidates: list[pathlib.Path] = []
    candidates.extend(p for p in ENGINE_DIR.glob("*.py"))
    candidates.extend(p for p in ENGINE_DIR.glob("*/__init__.py"))
    for p in sorted(candidates):
        if p.name == "__pycache__" or ".pytest_cache" in str(p):
            continue
        if parse_file(p) is None:
            key = str(p.relative_to(ROOT))
            seen_syntax_fail.add(key)
            issues.append({
                "severity": "P0", "check": "syntax", "module": key,
                "message": f"{key} 语法无法解析 —— 该模块无法被任何解释器加载",
            })
    return issues


def check_all_contract(mods: dict[str, pathlib.Path | None]) -> list[dict[str, Any]]:
    """检查 1：__all__ 声明 vs 真实可解析定义。

    对 __init__.py 里每个 from .mod import name 记录，去 mod 模块里核实
    name 是否真实存在。这能同时抓到两类错误：
      - __all__ 声明了但 __init__ 里从未 import（悬挂声明）
      - __init__ import 了但来源模块里没有该符号（ImportError）
    """
    issues: list[dict[str, Any]] = []
    if not INIT_FILE.is_file():
        return issues

    tree = parse_file(INIT_FILE)
    if tree is None:
        return [{"severity": "P0", "check": "all-contract",
                 "message": "engine/__init__.py 无法解析"}]

    # 1) 解析 __all__ 字面量
    declared: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "__all__":
                    if isinstance(n.value, (ast.List, ast.Tuple)):
                        for elt in n.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                declared.append(elt.value)
                    elif isinstance(n.value, ast.Set):
                        for elt in n.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                declared.append(elt.value)

    # 2) 解析 __init__ 里的所有 import 来源映射：name -> (来源模块, 原名, 行号)
    #    from .mod import sym    → provenance["sym"] = ("mod", "sym", lineno)
    #    from .mod import a as b → provenance["b"]   = ("mod", "a",   lineno)
    #    from . import mod       → AST 上 module=None, names=[alias("mod")]；
    #                              绑定的是模块对象本身，无来源符号可核实，单独标记。
    provenance: dict[str, tuple[str, str, int]] = {}
    module_imports: set[str] = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.ImportFrom):
            continue
        for a in n.names:
            bound = a.asname or a.name
            if n.module is None:
                # from . import mod —— mod 是模块对象
                module_imports.add(bound)
            elif a.name == n.module and (a.asname is None or a.asname == n.module):
                # from .mod import mod —— 同样绑定模块对象
                module_imports.add(bound)
            else:
                provenance[bound] = (n.module, a.name, n.lineno)

    # 3) 逐符号核实
    for sym in declared:
        if sym.startswith("_") or sym in module_imports:
            continue
        if sym not in provenance:
            issues.append({
                "severity": "P1", "check": "all-contract",
                "module": "engine/__init__.py", "symbol": sym,
                "message": f"__all__ 声明了 {sym!r}，但 __init__.py 中从未 import 它"
                           f" —— 访问 engine.{sym} 将抛 AttributeError",
            })
            continue
        mod_name, orig_name, lineno = provenance[sym]
        src = mods.get(mod_name)
        if src is None:
            issues.append({
                "severity": "P1", "check": "all-contract",
                "module": "engine/__init__.py", "symbol": sym,
                "message": f"__all__ 声明的 {sym!r} 来自 engine.{mod_name}，但该模块源文件不存在",
            })
            continue
        mod_tree = parse_file(src)
        if mod_tree is None:
            continue  # 已由检查 0 报告
        # 注意：这里必须查 orig_name 而非 sym。
        #   from .media_database import MEDIA as MEDIA_DATABASE
        # 来源模块里存在的是 MEDIA；MEDIA_DATABASE 只是 __init__ 里的本地别名。
        if orig_name not in defined_names(mod_tree):
            issues.append({
                "severity": "P1", "check": "all-contract",
                "module": f"engine/{mod_name}", "symbol": sym, "line": lineno,
                "message": f"__init__.py:{lineno} 从 engine.{mod_name} import {orig_name!r}"
                           f"（重命名为 {sym!r}），但来源模块里没有这个符号 —— import 时会 ImportError",
            })
    return issues


def check_top_level_side_effects(mods: dict[str, pathlib.Path | None]) -> list[dict[str, Any]]:
    """检查 2：模块顶层副作用。

    被 import 的瞬间就执行 main() / sys.exit() / subprocess 的模块，
    会让「import 一个库」变成「跑一遍它的 CLI」。engine 统一入口必须
    可被安全 import（Agent 会动态 introspect 它），所以这是阻断级。
    """
    issues: list[dict[str, Any]] = []
    for mod_name, path in mods.items():
        if path is None or not path.is_file() or path.name == "__init__.py":
            continue
        tree = parse_file(path)
        if tree is None:
            continue
        for n in tree.body:
            calls = []
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call):
                calls.append(n.value)
            elif isinstance(n, ast.Assign):
                calls.extend(v for v in ast.walk(n.value)
                             if isinstance(v, ast.Call) and v is n.value)
            for call in calls:
                try:
                    fname = ast.unparse(call.func)
                except Exception:
                    continue
                base = fname.split("(")[0].strip()
                if base == "main" or base.startswith("sys.exit") or base.startswith("os._exit"):
                    issues.append({
                        "severity": "P0", "check": "side-effect",
                        "module": f"engine/{mod_name}", "line": n.lineno,
                        "message": f"模块顶层直接调用 {base}() —— import 该模块即执行其副作用",
                    })
                elif base.startswith(("os.system", "subprocess.")):
                    issues.append({
                        "severity": "P1", "check": "side-effect",
                        "module": f"engine/{mod_name}", "line": n.lineno,
                        "message": f"模块顶层调用 {base}() —— import 即触发外部进程",
                    })
    return issues


def check_cli_guard(mods: dict[str, pathlib.Path | None]) -> list[dict[str, Any]]:
    """检查 3：CLI 入口守卫。

    有 def main() 却没有 if __name__ == "__main__" 守卫的模块，
    一旦被 import 就不会跑（安全），但也无法被 CLI 调用（不可用）。
    这是可用性问题而非安全性问题，标 P2 提示。
    """
    issues: list[dict[str, Any]] = []
    for mod_name, path in mods.items():
        if path is None or not path.is_file() or path.name == "__init__.py":
            continue
        tree = parse_file(path)
        if tree is None:
            continue
        has_main = any(isinstance(n, ast.FunctionDef) and n.name == "main" for n in tree.body)
        if not has_main:
            continue
        has_guard = False
        for n in tree.body:
            if isinstance(n, ast.If):
                try:
                    if "main" in ast.unparse(n.test):
                        has_guard = True
                except Exception:
                    pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
        if not has_guard:
            issues.append({
                "severity": "P2", "check": "cli-guard",
                "module": f"engine/{mod_name}",
                "message": f"定义了 main() 但没有 if __name__ == '__main__' 守卫 —— 无法作为 CLI 调用",
            })
    return issues


def check_entry_coverage(mods: dict[str, pathlib.Path | None]) -> list[dict[str, Any]]:
    """检查 4：统一入口覆盖率。

    engine 是「唯一真源」，但只有被 __init__.py 接入的模块才能被
    Agent / SDK 经统一入口检索到。未接入的孤儿模块等于对 Agent 隐身。
    这是信息性检查，不是错误 —— 有些模块（serve.py、demo_*.py）
    本就不该进统一入口。所以只输出统计，不产生阻断。
    """
    if not INIT_FILE.is_file():
        return []
    tree = parse_file(INIT_FILE)
    if tree is None:
        return []
    imported: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            # from .mod import sym   → module="mod"
            # from . import mod       → module=None, names=[alias("mod")]
            if n.module:
                imported.add(n.module.split(".")[0])
            else:
                for a in n.names:
                    imported.add(a.name.split(".")[0])
        elif isinstance(n, ast.Import):
            for a in n.names:
                imported.add(a.name.split(".")[0])
    orphan = sorted(m for m in mods if m not in imported and not m.startswith("_"))
    return [{
        "severity": "INFO", "check": "entry-coverage",
        "module": "engine/__init__.py",
        "stat": {
            "total_modules": len(mods),
            "wired": len([m for m in mods if m in imported]),
            "orphan": len(orphan),
            "orphan_list": orphan,
        },
        "message": f"engine 下 {len(mods)} 个模块中 {len(orphan)} 个未被 __init__.py 接入"
                   f"（占 {len(orphan) / max(len(mods), 1) * 100:.1f}%），"
                   f"对 Agent 的统一入口检索不可见",
    }]


# ────────────────────────────── 主流程 ──────────────────────────────

SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "INFO": 3}


def run_all() -> dict[str, Any]:
    mods = module_paths()
    issues: list[dict[str, Any]] = []
    issues += check_init_syntax(mods)
    issues += check_all_contract(mods)
    issues += check_top_level_side_effects(mods)
    issues += check_cli_guard(mods)
    issues += check_entry_coverage(mods)
    issues.sort(key=lambda i: (SEVERITY_ORDER.get(i["severity"], 9), i.get("module", "")))
    blocking = [i for i in issues if i["severity"] in ("P0", "P1")]
    return {"issues": issues, "blocking": blocking,
            "module_count": len(mods)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="engine 统一入口静态契约校验")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--verbose", action="store_true", help="展开 INFO 级别的孤儿模块清单")
    args = ap.parse_args(argv)

    if not ENGINE_DIR.is_dir():
        print(f"[engine-contract] ERROR: 未找到 {ENGINE_DIR}", file=sys.stderr)
        return 1

    result = run_all()
    issues = result["issues"]
    blocking = result["blocking"]

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if blocking else 0

    print("=" * 72)
    print("engine 统一入口契约校验（编译期静态分析，零执行）")
    print("=" * 72)
    print(f"模块数: {result['module_count']}")
    print(f"问题总数: {len(issues)}   阻断级(P0/P1): {len(blocking)}")
    print()

    shown_info = False
    for i in issues:
        sev = i["severity"]
        loc = f" {i.get('module')}" + (f":{i.get('line')}" if i.get("line") else "")
        if sev == "INFO" and not args.verbose:
            shown_info = True
            print(f"  [INFO] {loc}  {i['message']}")
            print("         （--verbose 查看完整清单）")
            continue
        print(f"  [{sev}] {loc}  {i['message']}")

    if not shown_info:
        print("  [INFO] 无信息级提示")

    print()
    if blocking:
        print(f"✗ 契约校验未通过：{len(blocking)} 个阻断级问题需修复后再合并")
        return 2
    print("✓ 契约完整")
    return 0


if __name__ == "__main__":
    sys.exit(main())
