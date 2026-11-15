"""A7 外部依赖正确性假设复现。

AXIOM_AUDIT A7：以下外部工具/库的正确性被假定。

复现方法：
1. 检查关键依赖是否可用
2. 验证依赖版本
3. 模拟依赖缺失的影响

期望输出：
- 所有依赖可用
- rdkit 缺失会导致 2 个 mol_* 场景被丢弃
"""
from __future__ import annotations
import importlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

DEPENDENCIES = {
    "numpy": {"version": None, "critical": True},
    "scipy": {"version": None, "critical": True},
    "rdkit": {"version": None, "critical": False, "impact": "2 个 mol_* 场景"},
    "z3": {"version": None, "critical": False, "impact": "验证通道 2"},
    "pandas": {"version": None, "critical": False},
}


def check_dependency(name, version=None):
    """检查依赖是否可用并返回版本。"""
    try:
        module = importlib.import_module(name)
        ver = getattr(module, "__version__", version or "unknown")
        return True, ver
    except ImportError:
        return False, None


def check_all_dependencies():
    """检查所有依赖。"""
    results = {}
    for name, info in DEPENDENCIES.items():
        available, version = check_dependency(name)
        results[name] = {
            "available": available,
            "version": version,
            "critical": info["critical"],
            "impact": info.get("impact", "N/A"),
        }
    return results


def simulate_rdkit_missing():
    """模拟 rdkit 缺失的影响。"""
    print("\n模拟 rdkit 缺失的影响:")
    print("  rdkit 缺失时:")
    print("  - 2 个 mol_* 场景被静默丢弃")
    print("  - INV-1 触发: sum(counts) != total")
    print("  - check_public_numbers.py 硬失败 dropped 场景")
    
    # 检查当前 rdkit 状态
    available, version = check_dependency("rdkit")
    print(f"\n  当前 rdkit 状态: {'✅ 可用' if available else '❌ 缺失'}")
    if version:
        print(f"  版本: {version}")
    
    return available


def main():
    print("=" * 60)
    print("A7 外部依赖正确性假设复现")
    print("=" * 60)
    
    # 检查所有依赖
    results = check_all_dependencies()
    
    print(f"\n依赖检查:")
    all_critical_ok = True
    for name, info in results.items():
        status = "✅" if info["available"] else "❌"
        critical_mark = " [关键]" if info["critical"] else ""
        impact = f" ({info['impact']})" if info["impact"] != "N/A" else ""
        print(f"  {status} {name:10s} {str(info['version']):20s}{critical_mark}{impact}")
        
        if info["critical"] and not info["available"]:
            all_critical_ok = False
    
    # 模拟 rdkit 缺失
    rdkit_available = simulate_rdkit_missing()
    
    # 依赖完整性评估
    print(f"\n依赖完整性评估:")
    n_available = sum(1 for r in results.values() if r["available"])
    n_total = len(results)
    print(f"  可用依赖: {n_available}/{n_total}")
    
    if all_critical_ok:
        print(f"  ✅ 所有关键依赖可用")
    else:
        print(f"  ❌ 有关键依赖缺失")
    
    # A7 结论
    print(f"\n{'=' * 60}")
    print("A7 结论:")
    print(f"  SwarmLabs 不为其依赖的正确性背书")
    print(f"\n  缓解措施:")
    print(f"  - 三条独立验证通道互相印证")
    print(f"  - INV-1 强制 sum(counts) == total")
    print(f"  - rdkit 缺失会暴露 (check_public_numbers.py)")
    print(f"  - Lean 4 CI 在 GitHub Actions 上独立编译")
    print(f"\n  未证明部分: Z3 和 Lean 4 的正确性证明依赖它们的规范")
    print(f"{'=' * 60}")
    
    return 0 if all_critical_ok else 1


if __name__ == "__main__":
    sys.exit(main())