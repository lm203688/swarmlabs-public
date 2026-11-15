#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""engine_module_count.py — engine/ 模块数的**唯一真源**。

背景（2026-10-17 查缺补漏 L-01）：
- 旧代码里 engine_modules 有两个不同的实现：
    * `scripts/build_public_facts.py` 用 `os.listdir(ENGINE_DIR)` 得到 101（**包含 `__init__.py`**）
    * `scripts/build_frontend_metrics.py` 用 `glob("*.py") if name != "__init__.py"` 得到 100（**排除包初始化**）
- 两个数字互相矛盾，`check_public_numbers.py` 无法判定哪个才是对的，
  只能靠"哪个门禁先跑"决定；这是**真源不唯一**的病根之一。

统一判据（本文件）：
- "engine 模块"指的是**可导入的引擎实现文件**（`surrogate.py`, `conformal.py` 等）。
- `__init__.py` 是 Python 包初始化，不是"引擎模块"，不计入。
- 排除 `__pycache__`、`.pyc`、`.pyi`（类型桩文件）。
- 排除 `_` 开头的私有实现细节（如 `_memoized.py`）—— 但这些目前不存在，
  规则留白以便将来扩展。

使用：
    from scripts.engine_module_count import engine_module_count
    n = engine_module_count()                    # 100（当前仓库）
    n_from = engine_module_count(engine_dir=...)  # 显式路径，便于测试

CLI：
    python scripts/engine_module_count.py         # 打印唯一真源数字
    python scripts/engine_module_count.py --json  # {"engine_modules": 100}

契约（被 `check_public_numbers.py` 与 CI 门禁共同依赖）：
- 只要 engine/ 下有一个新的 .py 模块被提交，本函数返回值必然 +1；
- `build_public_facts.py` 与 `build_frontend_metrics.py` 输出必须与此一致。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 唯一权威路径：仓库根 / engine
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENGINE_DIR = REPO_ROOT / "engine"


def _is_module_file(p: Path) -> bool:
    """判断一个 .py 文件是否算作 engine 模块。

    规则（写死，避免多处口径漂移）：
      1. 必须是 `.py` 文件（不是 `.pyi` / `.pyc` / `.pyc`）。
      2. 不能是 `__init__.py`（那是包初始化，不是"模块"）。
      3. 不能位于隐藏子目录（`.git` / `__pycache__` 等）。
      4. 只看 engine/ 顶层文件，不递归子目录（engine 保持扁平结构；
         如果将来引入子包，需要显式扩展此函数并同步注释）。
    """
    if p.suffix != ".py":
        return False
    if p.name == "__init__.py":
        return False
    return True


def engine_module_count(engine_dir: Path | str | None = None) -> int:
    """返回 engine/ 下可数模块数量（唯一真源）。"""
    d = Path(engine_dir) if engine_dir else DEFAULT_ENGINE_DIR
    if not d.exists():
        raise FileNotFoundError(
            f"engine_module_count(): engine dir not found: {d}"
        )
    # 只统计 engine/ 顶层 .py 文件（非递归），并排除 __init__.py。
    return sum(1 for p in d.iterdir() if p.is_file() and _is_module_file(p))


def engine_module_list(engine_dir: Path | str | None = None) -> list[str]:
    """返回 engine/ 下所有可数模块的文件名（排序，便于断言/调试）。"""
    d = Path(engine_dir) if engine_dir else DEFAULT_ENGINE_DIR
    if not d.exists():
        raise FileNotFoundError(f"engine_module_list(): engine dir not found: {d}")
    return sorted(p.name for p in d.iterdir() if p.is_file() and _is_module_file(p))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    custom = None
    for i, a in enumerate(argv):
        if a == "--engine-dir" and i + 1 < len(argv):
            custom = argv[i + 1]
            break
    n = engine_module_count(custom)
    if as_json:
        print(json.dumps({"engine_modules": n}, ensure_ascii=False))
    else:
        print(n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
