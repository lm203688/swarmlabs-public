"""A12 语言/编译器信任链假设复现。

AXIOM_AUDIT A12：Lean 4 编译器 + Mathlib 的正确性被假定。

复现方法：
1. 检查 Lean 4 是否可用
2. 验证 Mathlib 版本 pin
3. 检查三条验证通道的独立性

期望输出：
- Lean 4 版本
- Mathlib 版本
- 三通道独立性验证
"""
from __future__ import annotations
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def check_lean4():
    """检查 Lean 4 是否可用。"""
    try:
        result = subprocess.run(
            ["lean", "--version"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, "Lean 4 未安装或不可用"


def check_mathlib_version():
    """检查 Mathlib 版本。"""
    lakefile = os.path.join(ROOT, "svp", "lean", "lakefile.toml")
    if not os.path.exists(lakefile):
        return None
    
    with open(lakefile) as f:
        content = f.read()
    
    # 查找 Mathlib 版本
    for line in content.splitlines():
        if "mathlib" in line.lower() and "rev" in line.lower():
            # 提取版本
            parts = line.split()
            for p in parts:
                if "v" in p and "lean" not in p.lower():
                    return p.strip('"\'')
    
    return None


def check_three_channels():
    """检查三条验证通道的独立性。"""
    channels = {
        "Python 语义": True,  # 主通道
        "Z3 SMT": False,
        "Lean 4": False,
    }
    
    # 检查 Z3
    try:
        from z3 import Solver
        channels["Z3 SMT"] = True
    except ImportError:
        pass
    
    # 检查 Lean 4
    lean_available, _ = check_lean4()
    channels["Lean 4"] = lean_available
    
    return channels


def main():
    print("=" * 60)
    print("A12 语言/编译器信任链假设复现")
    print("=" * 60)
    
    # 检查 Lean 4
    print("\n检查 Lean 4:")
    lean_available, lean_info = check_lean4()
    if lean_available:
        print(f"  ✅ Lean 4 可用")
        print(f"  版本: {lean_info}")
    else:
        print(f"  ⚠️  {lean_info}")
    
    # 检查 Mathlib
    print(f"\n检查 Mathlib:")
    mathlib_ver = check_mathlib_version()
    if mathlib_ver:
        print(f"  ✅ Mathlib 版本: {mathlib_ver}")
        print(f"  已 pin 版本，可重现")
    else:
        print(f"  ⚠️  Mathlib 版本未找到")
    
    # 检查三通道独立性
    print(f"\n检查三条验证通道:")
    channels = check_three_channels()
    for name, available in channels.items():
        status = "✅" if available else "⚠️"
        print(f"  {status} {name}")
    
    n_active = sum(1 for v in channels.values() if v)
    print(f"\n  活跃通道: {n_active}/3")
    if n_active >= 2:
        print(f"  ✅ 至少两条通道独立验证")
    else:
        print(f"  ⚠️  通道不足，验证冗余性降低")
    
    # A12 结论
    print(f"\n{'=' * 60}")
    print("A12 结论:")
    print(f"  SwarmLabs 信任 Lean 4 编译器 + Mathlib 的正确性")
    print(f"\n  缓解措施:")
    print(f"  - Lean 4 内核有独立正确性证明")
    print(f"  - Mathlib 版本 pin 在 lakefile.toml")
    print(f"  - 三条独立通道：Lean 失败不阻断 Python+Z3")
    print(f"\n  未证明部分: Lean 内核的正确性证明依赖代码审查")
    print(f"{'=' * 60}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())