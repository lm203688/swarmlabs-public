"""A1 场景代表性假设复现。

AXIOM_AUDIT A1：82 场景的训练/测试分布是否足够代表目标领域真实分布？

复现方法：
1. 检查每个场景的 domain 覆盖度
2. 统计微生物 vs 其他领域比例
3. 验证场景分布是否均匀

期望输出：
- 微生物域占比 > 50%（确认领域集中）
- 最大单一领域场景数
- 领域多样性指数
"""
from __future__ import annotations
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def load_scenario_registry():
    """从 scenario_registry 加载场景列表。"""
    try:
        from engine.scenario_registry import SCENARIOS
        return list(SCENARIOS.values())
    except Exception:
        pass
    # Fallback: 从 gate_ledger 加载
    ledger_path = os.path.join(ROOT, "data", "gate_ledger.json")
    if os.path.exists(ledger_path):
        with open(ledger_path) as f:
            ledger = json.load(f)
        return ledger.get("results", [])
    return []


def compute_domain_distribution(scenarios):
    """计算领域分布。"""
    from collections import Counter
    domains = [s.get("domain", "unknown") for s in scenarios]
    return Counter(domains)


def compute_domain_diversity(domains):
    """计算领域多样性（Shannon 指数）。"""
    from collections import Counter
    import math
    counts = Counter(domains)
    total = sum(counts.values())
    if total == 0:
        return 0.0
    entropy = -sum((c / total) * math.log2(c / total) for c in counts.values() if c > 0)
    return entropy


def main():
    print("=" * 60)
    print("A1 场景代表性假设复现")
    print("=" * 60)

    scenarios = load_scenario_registry()
    print(f"\n加载场景数: {len(scenarios)}")

    if not scenarios:
        print("❌ 无法加载场景数据")
        return 1

    # 领域分布
    domains = [s.get("domain", s.get("domain_label", "unknown")) for s in scenarios]
    domain_counts = compute_domain_distribution(scenarios)

    print(f"\n领域分布:")
    for domain, count in domain_counts.most_common(10):
        pct = count / len(scenarios) * 100
        print(f"  {domain:30s}: {count:3d} ({pct:5.1f}%)")

    # 微生物占比
    micro_domains = [d for d in domains if "micro" in d.lower() or "microbiolog" in d.lower()
                     or "biolog" in d.lower() or "ecoli" in d.lower()]
    micro_ratio = len(micro_domains) / len(scenarios) * 100
    print(f"\n微生物/生物类占比: {len(micro_domains)}/{len(scenarios)} = {micro_ratio:.1f}%")

    # 领域多样性
    diversity = compute_domain_diversity(domains)
    max_diversity = math.log2(len(domain_counts)) if len(domain_counts) > 1 else 0
    normalized_diversity = diversity / max_diversity if max_diversity > 0 else 0
    print(f"\n领域多样性:")
    print(f"  Shannon 指数: {diversity:.3f}")
    print(f"  归一化多样性: {normalized_diversity:.3f} (1.0=完全均匀)")
    print(f"  领域数量: {len(domain_counts)}")

    # A1 结论
    print(f"\n{'=' * 60}")
    print("A1 结论:")
    if micro_ratio > 50:
        print(f"  ⚠️ 领域集中: 微生物占比 {micro_ratio:.1f}% > 50%")
        print(f"  这意味着 V&V 结论不能推广到非微生物领域")
    else:
        print(f"  ✅ 领域分布相对均衡: 微生物占比 {micro_ratio:.1f}%")

    if normalized_diversity < 0.7:
        print(f"  ⚠️ 多样性不足: {normalized_diversity:.3f} < 0.7")
    else:
        print(f"  ✅ 多样性充足: {normalized_diversity:.3f}")

    print(f"\n  未证明部分: 没有第三方独立验证场景分布覆盖")
    print(f"  缓解措施: INV-2 强制公开 REFUTED 场景")
    print(f"{'=' * 60}")

    return 0


if __name__ == "__main__":
    import math
    sys.exit(main())