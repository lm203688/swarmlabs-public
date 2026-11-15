#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""综合虚拟实验室（VLab）零依赖基座。

为什么单独一个模块
------------------
`engine/__init__.py` 会拉起 numpy 依赖链（surrogate / guard / emulator ...）。
而 VLab 的**入口层**（工作单契约、专业脚手架、锚定筛选、交付契约）必须在
**零依赖环境**下也能跑：

  - CI 的干净解释器（故意不装 numpy，以检出偷依赖）；
  - CF Worker / 纯静态托管侧的门禁复算；
  - 第三方 clone 后离线复验一次交付。

所以这一层只 import 标准库，并且**不 import engine 包**——需要用到
`verification_gate.py` 的阈值/指纹时，按**文件路径**加载它（它自身也只
import 标准库）。这是 `engine/vv_package.py` 已确立的同一模式，这里把它
抽成共享基座，避免每个新模块各抄一份。

设计红线
--------
- 任何"缺文件"不得被降级成默认值：读不到就是读不到（返回 None / 抛错），
  由调用方决定是 fail-closed 阻断还是记成可读事实。
- 摘要一律用 `json.dumps(sort_keys=True, ensure_ascii=False)` 规范化后取
  sha256——保证「同样的语义内容 → 同样的摘要」，跨机器可复算。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_DIR = os.path.join(ROOT, "engine")
GATE_MODULE_PATH = os.path.join(ENGINE_DIR, "verification_gate.py")

#: 本基座承诺只依赖标准库；selfcheck 会断言这一点。
STDLIB_ONLY = True


# --------------------------------------------------------------------- 加载

def load_module_by_path(alias: str, path: str):
    """按**文件路径**加载一个模块，绕开包的 `__init__.py`。

    `import engine.verification_gate` 会先执行 `engine/__init__.py`，把 numpy
    拉进一个本该零依赖的层。这里直接 spec-loader，只执行目标文件本身。
    """
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module by path: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_gate():
    """加载 `engine/verification_gate.py`（阈值 / 指纹 / 判定映射的唯一真源）。"""
    return load_module_by_path("_vlab_gate", GATE_MODULE_PATH)


def load_domains():
    """加载 `engine/domains.py`（9 大类专业 taxonomy 的唯一真源）。

    `domains.py` 顶层无第三方依赖（`active_domains()` 才去 import scripts），
    所以按路径加载是安全的，且保证专业清单**只有一份**。
    """
    return load_module_by_path(
        "_vlab_domains", os.path.join(ENGINE_DIR, "domains.py")
    )


# --------------------------------------------------------------------- 摘要

def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def canonical_sha256(obj: Any) -> str:
    """规范化摘要：同语义内容必得同 digest，跨机器可复算。"""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def short(digest: Optional[str], n: int = 12) -> str:
    return (digest or "")[:n]


# --------------------------------------------------------------------- 时间

def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_stamp() -> str:
    """紧凑时间戳，用于文件名（不含冒号，Windows 安全）。"""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# --------------------------------------------------------------------- 文件

def read_json(path: str):
    """读 JSON；不存在或损坏返回 None（不抛，由调用方 fail-closed）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_json(path: str, obj: Any) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")


def write_text(path: str, text: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ------------------------------------------------------------------ 数值工具

def is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def is_finite(x: Any) -> bool:
    if not is_number(x):
        return False
    f = float(x)
    return f == f and f not in (float("inf"), float("-inf"))


def nonempty_str(x: Any) -> bool:
    return isinstance(x, str) and bool(x.strip())


__all__ = [
    "ROOT", "ENGINE_DIR", "GATE_MODULE_PATH", "STDLIB_ONLY",
    "load_module_by_path", "load_gate", "load_domains",
    "canonical_json", "canonical_sha256", "sha256_file", "short",
    "utc_now", "utc_stamp", "read_json", "write_json", "write_text",
    "is_number", "is_finite", "nonempty_str",
]
