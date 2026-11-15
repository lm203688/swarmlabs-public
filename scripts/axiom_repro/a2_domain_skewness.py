"""A2 微生物领域集中假设复现。

AXIOM_AUDIT A2：82 场景中微生物/生物类占 54.9%（45/82），
V&V 引擎在微生物领域的表现不等于在其他领域的表现。

复现方法：
1. 统计各领域场景数
2. 计算领域集中度（Herfindahl 指数）
3. 验证跨领域 V&V 一致性

期望输出：
- 微生物域占比 54.9%
- Herfindahl 指数 > 0.3（高集中）
- 跨领域 PASS 率差异
"""
from __future__ import annotations
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def load_gate_ledger():
    """加载 gate_ledger 获取场景结果。"""
    path = os.path.join(ROOT, "data", "gate_ledger.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def compute_herfindahl(domain_counts, total):
    """计算 Herfindahl-Hirschman 指数（领域集中度）。"""
    hhi = sum((count / total) ** 2 for count in domain_counts.values())
    return hhi


def main():
    print("=" * 60)
    print("A2 微生物领域集中假设复现")
    print("=" * 60)

    ledger = load_gate_ledger()
    if not ledger:
        print("❌ 无法加载 gate_ledger.json")
        return 1

    results = ledger.get("results", [])
    print(f"\n加载场景结果: {len(results)}")

    if not results:
        print("❌ 无场景结果")
        return 1

    # 统计领域分布
    domain_counts = {}
    domain_pass = {}
    for r in results:
        domain = r.get("domain", r.get("domain_label", "unknown"))
        status = r.get("status", r.get("decision", "unknown"))

        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        domain_pass.setdefault(domain, {"pass": 0, "total": 0})
        domain_pass[domain]["total"] += 1
        if status in ("PASS", "pass"):
            domain_pass[domain]["pass"] += 1

    total = sum(domain_counts.values())
    print(f"\n总场景数: {total}")

    # 微生物域统计
    micro_domains = []
    micro_total = 0
    for domain, count in domain_counts.items():
        if any(kw in domain.lower() for kw in ["micro", "biolog", "ecoli", "e_coli", "bacteri"]):
            micro_total += count
            micro_domains.append(domain)

    micro_ratio = micro_total / total * 100
    print(f"\n微生物/生物类场景: {micro_total}/{total} = {micro_ratio:.1f}%")
    print(f"  涉及领域: {', '.join(micro_domains[:5])}")

    # Herfindahl 指数
    hhi = compute_herfindahl(domain_counts, total)
    print(f"\n领域集中度 (Herfindahl):")
    print(f"  HHI = {hhi:.4f}")
    if hhi > 0.25:
        print(f"  ⚠️ 高集中 (>0.25): 单一领域主导")
    elif hhi > 0.15:
        print(f"  ⚠️ 中集中 (0.15-0.25)")
    else:
        print(f"  ✅ 低集中 (<0.15): 领域分布较均衡")

    # 跨领域 PASS 率
    print(f"\n跨领域 PASS 率:")
    for domain, stats in sorted(domain_pass.items(), key=lambda x: x[1]["total"], reverse=True)[:10]:
        if stats["total"] >= 3:  # 只显示有统计意义的领域
            pass_rate = stats["pass"] / stats["total"] * 100
            print(f"  {domain:30s}: {pass_rate:5.1f}% ({stats['pass']}/{stats['total']})")

    # A2 结论
    print(f"\n{'=' * 60}")
    print("A2 结论:")
    if micro_ratio > 50:
        print(f"  ⚠️ 确认领域集中: 微生物占比 {micro_ratio:.1f}%")
        print(f"  化学场景的 V&V 结果不能推广到微生物场景")
        print(f"  论文结论的领域外推性受限")
    print(f"\n  缓解措施: 论文方法章节明确声明领域分布")
    print(f"  不宣称'跨领域验证'")
    print(f"{'=' * 60}")

    return 0


if __name__ == "__main__":
    sys.exit(main())