"""expected_improvement 累积 miss 率追踪模块。

响应评估报告 P0-4：建立 miss 率追踪表，让「实验建议」真正可证伪。

核心问题
--------
experiment_proposal.py 已有 verify_prediction() 函数能判断单次预测是否命中，
但**缺少累积追踪**：

1. 不知道历史 miss 率是多少
2. 不知道预测器是否越来越准（还是越来越差）
3. 没有机制暴露「这个预测器不可信」

没有累积追踪，expected_improvement 就是一个不可证伪的声明。

本模块提供
--------
1. MissTracker：累积记录每次 verify_prediction 的结果
2. 按场景/领域分组的 miss 率统计
3. miss 率趋势检测（是否恶化）
4. 与 fail-closed 门禁集成（miss 率过高时阻断实验建议）
5. 导出可审计报告

诚实边界
--------
- miss 率统计依赖足够的样本量（< 5 次验证时不给出结论）
- 不宣称 miss 率能预测未来准确性（past ≠ future）
- 不自动调整 EXPECTED_IMPROVEMENT_TOL（那需要人工判断）
"""

from __future__ import annotations

import json
import os
import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
MISS_TRACKER_PATH = os.path.join(DATA, "miss_tracker.json")

#: miss 率阈值：超过此值时发出警告
MISS_RATE_WARN_THRESHOLD = 0.4   # 40% miss 率
MISS_RATE_CRITICAL_THRESHOLD = 0.6  # 60% miss 率（预测器不可信）
#: 最少验证次数：低于此值不给出结论
MIN_VERIFICATIONS_FOR_CONCLUSION = 5


@dataclass
class PredictionRecord:
    """单次预测验证记录。"""
    key: str                          # 场景 key
    domain: str = ""                  # 领域
    #: 预测的 rmse_rel。``None`` = 收益未知（PR-09 后启发式/占位建议无精确预测）。
    predicted_rmse_rel: Optional[float] = None
    observed_rmse_rel: float = 0.0
    verdict: str = "unverified"       # verified | prediction_missed | unverified
    tolerance: float = 0.30
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat(timespec="seconds"))

    @property
    def improvement_ratio(self) -> Optional[float]:
        """实际改善倍数。预测未知（None）时返回 None，不伪造数字。"""
        if self.predicted_rmse_rel is None:
            return None
        if self.predicted_rmse_rel <= 1e-12:
            return 1.0
        return self.predicted_rmse_rel / max(self.observed_rmse_rel, 1e-12)

    def to_dict(self) -> dict:
        d = asdict(self)
        ratio = self.improvement_ratio
        d["improvement_ratio"] = (
            round(ratio, 4) if ratio is not None else None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PredictionRecord":
        pred = d.get("predicted_rmse_rel", None)
        return cls(
            key=d["key"],
            domain=d.get("domain", ""),
            # PR-09：允许 None（收益未知），不再回退 0.0——0.0 会被误读为
            # 「预测到零误差」这种不可能的精确断言。
            predicted_rmse_rel=pred if pred is not None else None,
            observed_rmse_rel=d.get("observed_rmse_rel", 0.0),
            verdict=d.get("verdict", "unverified"),
            tolerance=d.get("tolerance", 0.30),
            timestamp=d.get("timestamp", ""),
        )


@dataclass
class MissRateStats:
    """miss 率统计。"""
    total: int = 0                    # 总验证次数
    verified: int = 0                 # 命中次数
    missed: int = 0                   # 未命中次数
    unverified: int = 0               # 未验证次数
    miss_rate: float = 0.0            # miss 率（missed / (verified + missed)）
    by_domain: dict = field(default_factory=dict)  # 按领域分组
    by_scenario: dict = field(default_factory=dict)  # 按场景分组
    trend: str = "insufficient_data"  # improving | stable | degrading | insufficient_data
    trend_detail: str = ""
    warning: str = ""                 # 警告信息（如有）

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MissRateStats":
        return cls(
            total=d.get("total", 0),
            verified=d.get("verified", 0),
            missed=d.get("missed", 0),
            unverified=d.get("unverified", 0),
            miss_rate=d.get("miss_rate", 0.0),
            by_domain=d.get("by_domain", {}),
            by_scenario=d.get("by_scenario", {}),
            trend=d.get("trend", "insufficient_data"),
            trend_detail=d.get("trend_detail", ""),
            warning=d.get("warning", ""),
        )


class MissTracker:
    """expected_improvement 累积 miss 率追踪器。

    核心能力：
    1. 记录每次 verify_prediction 的结果
    2. 计算累积 miss 率
    3. 按场景/领域分组统计
    4. 检测 miss 率趋势（是否恶化）
    5. 与 fail-closed 门禁集成

    使用示例::

        tracker = MissTracker()

        # 记录一次验证结果
        tracker.record(
            key="micro_lag_ecoli",
            domain="microbiology",
            predicted_rmse_rel=0.12,
            observed_rmse_rel=0.15,
            verdict="prediction_missed",
        )

        # 查询累积统计
        stats = tracker.get_stats()
        if stats.miss_rate > 0.4:
            print(f"警告：miss 率 {stats.miss_rate:.0%} 过高")
    """

    def __init__(self, path: str = MISS_TRACKER_PATH):
        self.path = path
        self._records: list[PredictionRecord] = []
        self._load()

    def _load(self):
        """加载历史记录。"""
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._records = [PredictionRecord.from_dict(r) for r in data.get("records", [])]
            except Exception:
                self._records = []

    def save(self):
        """保存记录到磁盘。"""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        data = {
            "version": 1,
            "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "records": [r.to_dict() for r in self._records],
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def record(self, key: str, predicted_rmse_rel: Optional[float],
               observed_rmse_rel: float,
               verdict: str = "unverified", domain: str = "", tolerance: float = 0.30):
        """记录一次预测验证结果。

        Args:
            key: 场景 key
            predicted_rmse_rel: 预测的 rmse_rel
            observed_rmse_rel: 实测的 rmse_rel
            verdict: 判定结果（verified/prediction_missed/unverified）
            domain: 领域（可选）
            tolerance: 容差（可选）
        """
        rec = PredictionRecord(
            key=key,
            domain=domain,
            predicted_rmse_rel=predicted_rmse_rel,
            observed_rmse_rel=observed_rmse_rel,
            verdict=verdict,
            tolerance=tolerance,
        )
        self._records.append(rec)
        self.save()

    def get_stats(self, scenario_key: str = None, domain: str = None) -> MissRateStats:
        """计算 miss 率统计。

        Args:
            scenario_key: 按场景 key 过滤（可选）
            domain: 按领域过滤（可选）

        Returns:
            MissRateStats 统计结果
        """
        records = self._records

        # 过滤
        if scenario_key:
            records = [r for r in records if r.key == scenario_key]
        if domain:
            records = [r for r in records if r.domain == domain]

        stats = MissRateStats()

        for r in records:
            stats.total += 1
            if r.verdict == "verified":
                stats.verified += 1
            elif r.verdict == "prediction_missed":
                stats.missed += 1
            else:
                stats.unverified += 1

        # 计算 miss 率
        verified_or_missed = stats.verified + stats.missed
        if verified_or_missed > 0:
            stats.miss_rate = stats.missed / verified_or_missed
        else:
            stats.miss_rate = 0.0

        # 按领域分组
        if not domain:  # 只有全局统计时才分组
            domain_records = {}
            for r in self._records:
                d = r.domain or "unknown"
                if d not in domain_records:
                    domain_records[d] = {"total": 0, "verified": 0, "missed": 0, "miss_rate": 0.0}
                domain_records[d]["total"] += 1
                if r.verdict == "verified":
                    domain_records[d]["verified"] += 1
                elif r.verdict == "prediction_missed":
                    domain_records[d]["missed"] += 1
            for d, dr in domain_records.items():
                vm = dr["verified"] + dr["missed"]
                dr["miss_rate"] = dr["missed"] / vm if vm > 0 else 0.0
            stats.by_domain = domain_records

        # 按场景分组
        if not scenario_key:  # 只有全局统计时才分组
            scenario_records = {}
            for r in self._records:
                k = r.key
                if k not in scenario_records:
                    scenario_records[k] = {"total": 0, "verified": 0, "missed": 0, "miss_rate": 0.0}
                scenario_records[k]["total"] += 1
                if r.verdict == "verified":
                    scenario_records[k]["verified"] += 1
                elif r.verdict == "prediction_missed":
                    scenario_records[k]["missed"] += 1
            for k, sr in scenario_records.items():
                vm = sr["verified"] + sr["missed"]
                sr["miss_rate"] = sr["missed"] / vm if vm > 0 else 0.0
            stats.by_scenario = scenario_records

        # 检测趋势
        stats.trend, stats.trend_detail = self._detect_trend(records)

        # 生成警告
        if verified_or_missed >= MIN_VERIFICATIONS_FOR_CONCLUSION:
            if stats.miss_rate >= MISS_RATE_CRITICAL_THRESHOLD:
                stats.warning = (
                    f"CRITICAL: miss 率 {stats.miss_rate:.0%} 超过 "
                    f"{MISS_RATE_CRITICAL_THRESHOLD:.0%}，预测器不可信。"
                    f"建议停止使用该预测器生成实验建议。"
                )
            elif stats.miss_rate >= MISS_RATE_WARN_THRESHOLD:
                stats.warning = (
                    f"WARN: miss 率 {stats.miss_rate:.0%} 超过 "
                    f"{MISS_RATE_WARN_THRESHOLD:.0%}，预测器准确性存疑。"
                    f"建议人工复核实验建议。"
                )
            elif stats.trend == "degrading":
                stats.warning = (
                    f"WARN: miss 率趋势恶化（{stats.trend_detail}）。"
                    f"建议检查预测器是否退化。"
                )

        return stats

    def _detect_trend(self, records: list[PredictionRecord]) -> tuple[str, str]:
        """检测 miss 率趋势。

        Returns:
            (trend, detail) 元组
            trend: improving | stable | degrading | insufficient_data
        """
        # 只统计已验证的记录
        verified_records = [r for r in records if r.verdict in ("verified", "prediction_missed")]

        if len(verified_records) < MIN_VERIFICATIONS_FOR_CONCLUSION:
            return "insufficient_data", f"验证次数不足（{len(verified_records)} < {MIN_VERIFICATIONS_FOR_CONCLUSION}）"

        # 按时间顺序排列
        verified_records.sort(key=lambda r: r.timestamp)

        # 计算前半段和后半段的 miss 率
        n = len(verified_records)
        mid = n // 2
        first_half = verified_records[:mid]
        second_half = verified_records[mid:]

        first_misses = sum(1 for r in first_half if r.verdict == "prediction_missed")
        second_misses = sum(1 for r in second_half if r.verdict == "prediction_missed")
        first_rate = first_misses / len(first_half) if first_half else 0.0
        second_rate = second_misses / len(second_half) if second_half else 0.0

        diff = second_rate - first_rate

        if diff > 0.1:  # 后半段比前半段差 10% 以上
            return "degrading", f"miss 率从 {first_rate:.0%} 上升到 {second_rate:.0%}（+{diff:.0%}）"
        elif diff < -0.1:  # 后半段比前半段好 10% 以上
            return "improving", f"miss 率从 {first_rate:.0%} 下降到 {second_rate:.0%}（{diff:.0%}）"
        else:
            return "stable", f"miss 率稳定在 {first_rate:.0%} ~ {second_rate:.0%}"

    def should_block(self, scenario_key: str = None, domain: str = None) -> tuple[bool, str]:
        """判断是否应该阻断实验建议（与 fail-closed 门禁集成）。

        Returns:
            (should_block, reason) 元组
        """
        stats = self.get_stats(scenario_key=scenario_key, domain=domain)

        if stats.total < MIN_VERIFICATIONS_FOR_CONCLUSION:
            return False, f"验证次数不足（{stats.total} < {MIN_VERIFICATIONS_FOR_CONCLUSION}），暂不阻断"

        if stats.miss_rate >= MISS_RATE_CRITICAL_THRESHOLD:
            return True, stats.warning

        if stats.trend == "degrading":
            return True, stats.warning

        return False, "miss 率在可接受范围内"

    def export_report(self) -> str:
        """导出 Markdown 格式的 miss 率报告。"""
        stats = self.get_stats()

        lines = [
            "# Expected Improvement Miss Rate Report",
            f"\nGenerated: {datetime.datetime.now().isoformat()}\n",
            f"## Overall Statistics\n",
            f"- Total verifications: {stats.total}",
            f"- Verified (hit): {stats.verified}",
            f"- Missed: {stats.missed}",
            f"- Unverified: {stats.unverified}",
            f"- **Miss rate: {stats.miss_rate:.1%}**",
            f"- Trend: {stats.trend} ({stats.trend_detail})",
        ]

        if stats.warning:
            lines.append(f"\n## ⚠️ Warning\n")
            lines.append(f"{stats.warning}\n")

        if stats.by_domain:
            lines.append("\n## By Domain\n")
            lines.append("| Domain | Total | Missed | Miss Rate |")
            lines.append("|---|---|---|---|")
            for domain, dr in sorted(stats.by_domain.items()):
                lines.append(f"| {domain} | {dr['total']} | {dr['missed']} | {dr['miss_rate']:.1%} |")

        if stats.by_scenario:
            lines.append("\n## By Scenario (Top 10)\n")
            lines.append("| Scenario | Total | Missed | Miss Rate |")
            lines.append("|---|---|---|---|")
            sorted_scenarios = sorted(stats.by_scenario.items(), key=lambda x: x[1]["total"], reverse=True)[:10]
            for key, sr in sorted_scenarios:
                lines.append(f"| {key} | {sr['total']} | {sr['missed']} | {sr['miss_rate']:.1%} |")

        if self._records:
            lines.append("\n## Recent Records (Last 10)\n")
            lines.append("| Timestamp | Scenario | Predicted | Observed | Verdict |")
            lines.append("|---|---|---|---|---|")
            for r in self._records[-10:]:
                pred_str = (f"{r.predicted_rmse_rel:.4f}"
                            if r.predicted_rmse_rel is not None else "未知")
                lines.append(
                    f"| {r.timestamp[:10]} | {r.key} | {pred_str} | "
                    f"{r.observed_rmse_rel:.4f} | {r.verdict} |"
                )

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """导出完整状态为字典。"""
        return {
            "records": [r.to_dict() for r in self._records],
            "stats": self.get_stats().to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict, path: str = MISS_TRACKER_PATH) -> "MissTracker":
        """从字典恢复状态。"""
        tracker = cls(path=path)
        tracker._records = [PredictionRecord.from_dict(r) for r in d.get("records", [])]
        return tracker


# ---------------------------------------------------------------------------
# 便捷函数：与 verify_prediction 集成
# ---------------------------------------------------------------------------

def record_verification(
    tracker: MissTracker,
    proposal,  # ExperimentProposal
    observed_rmse_rel: float,
    observed_coverage: float = None,
) -> dict:
    """从 verify_prediction 的结果记录到 tracker。

    Args:
        tracker: MissTracker 实例
        proposal: ExperimentProposal（来自 experiment_proposal.propose_experiment）
        observed_rmse_rel: 实测 rmse_rel
        observed_coverage: 实测 coverage（可选）

    Returns:
        verify_prediction 的结果字典
    """
    from .experiment_proposal import verify_prediction

    result = verify_prediction(proposal, observed_rmse_rel, observed_coverage)

    # 记录到 tracker
    if result.get("verdict") in ("verified", "prediction_missed"):
        tracker.record(
            key=result.get("key", proposal.key),
            # PR-09：默认 None（未知），绝不把缺省误读为 0.0（「零误差」）
            predicted_rmse_rel=result.get("predicted_rmse_rel", None),
            observed_rmse_rel=result.get("observed_rmse_rel", 0.0),
            verdict=result["verdict"],
            domain=getattr(proposal, "domain", ""),
            tolerance=result.get("tolerance", 0.30),
        )

    return result


def check_tracker_health() -> dict:
    """检查 tracker 整体健康状况（供 CI 门禁使用）。

    Returns:
        健康状态字典
    """
    tracker = MissTracker()
    stats = tracker.get_stats()
    should_block, reason = tracker.should_block()

    return {
        "total_verifications": stats.total,
        "miss_rate": stats.miss_rate,
        "trend": stats.trend,
        "should_block": should_block,
        "reason": reason,
        "warning": stats.warning,
        "by_domain_count": len(stats.by_domain),
        "by_scenario_count": len(stats.by_scenario),
    }


def __main__():
    """Self-test: 验证模块基本功能。"""
    print("Miss Tracker Self-Test")
    print("=" * 60)

    test_path = "/tmp/test_miss_tracker.json"

    # 清理测试文件
    if os.path.exists(test_path):
        os.remove(test_path)

    tracker = MissTracker(path=test_path)

    # 测试 1: 基本记录
    print("\n1. Testing basic recording...")
    tracker.record("micro_lag_ecoli", 0.12, 0.15, "prediction_missed", "microbiology")
    tracker.record("micro_lag_ecoli", 0.10, 0.12, "verified", "microbiology")
    tracker.record("chem_arrhenius", 0.08, 0.09, "verified", "chemistry")
    tracker.record("chem_arrhenius", 0.08, 0.15, "prediction_missed", "chemistry")
    tracker.record("neuro_lif", 0.05, 0.06, "verified", "neuroscience")
    print(f"   Records: {len(tracker._records)} ✓")

    # 测试 2: 全局统计
    print("\n2. Testing global stats...")
    stats = tracker.get_stats()
    print(f"   Total: {stats.total}")
    print(f"   Verified: {stats.verified}")
    print(f"   Missed: {stats.missed}")
    print(f"   Miss rate: {stats.miss_rate:.1%}")
    print(f"   Trend: {stats.trend} ({stats.trend_detail})")
    assert stats.total == 5
    assert stats.verified == 3
    assert stats.missed == 2

    # 测试 3: 按场景过滤
    print("\n3. Testing scenario filter...")
    stats_ecoli = tracker.get_stats(scenario_key="micro_lag_ecoli")
    print(f"   micro_lag_ecoli: {stats_ecoli.total} records, miss_rate={stats_ecoli.miss_rate:.1%}")
    assert stats_ecoli.total == 2
    assert stats_ecoli.missed == 1

    # 测试 4: 按领域过滤
    print("\n4. Testing domain filter...")
    stats_chem = tracker.get_stats(domain="chemistry")
    print(f"   chemistry: {stats_chem.total} records, miss_rate={stats_chem.miss_rate:.1%}")
    assert stats_chem.total == 2

    # 测试 5: 阻断判断
    print("\n5. Testing block decision...")
    # 当前 40% miss 率，刚好在警告阈值
    should_block, reason = tracker.should_block()
    print(f"   Should block: {should_block}, reason: {reason}")

    # 测试 6: 趋势检测（模拟恶化）
    print("\n6. Testing trend detection (degrading)...")
    # 添加更多 miss 记录来制造恶化趋势
    for i in range(5):
        tracker.record(f"scenario_{i}", 0.10, 0.20, "prediction_missed", "test")
    stats2 = tracker.get_stats()
    print(f"   After adding misses: {stats2.total} records, miss_rate={stats2.miss_rate:.1%}")
    print(f"   Trend: {stats2.trend} ({stats2.trend_detail})")

    # 测试 7: 报告导出
    print("\n7. Testing report export...")
    report = tracker.export_report()
    assert "Miss Rate Report" in report
    assert "miss_rate" in report.lower() or "miss rate" in report.lower()
    print(f"   Report length: {len(report)} chars ✓")

    # 测试 8: 健康检查
    print("\n8. Testing health check...")
    health = check_tracker_health()
    print(f"   Health: {health}")

    # 清理测试文件
    if os.path.exists(test_path):
        os.remove(test_path)

    print("\n" + "=" * 60)
    print("All tests passed! ✓")


if __name__ == "__main__":
    __main__()
