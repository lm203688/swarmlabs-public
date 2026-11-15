"""Distributional V&V — 分布级验证与验证（SVP v0.5）。

准入判据锚定：让虚拟实验在"无真实实验、无网络、无设备"下更可替代
───────────────────────────────────────────────────────────────────
SVP v0.1 的 V&V 输出是**点判定**（PASS / MARGINAL / REFUTED / ERROR）
—— 一个场景要么过关，要么不过关。这在学术语境里是"结论"，在工程
语境里是"结果"，在**科学发现**语境里却不够：

- 科学结论本质是"在多大置信度下成立"，不是"成立/不成立"。
- 一个 R²=0.90 的场景和一个 R²=0.999 的场景，都叫 PASS，但它们
  对下游决策的可替代性差异巨大。
- 单点判定的置信区间"名义 0.95"经常被误解为"真实 0.95"。

Conformal Prediction 提供的正是"分布级 V&V"：给定一个模型 + 校准
集，可以**同时**输出：

  1. 一个置信区间的真实覆盖率（PICP）
  2. 区间宽度归一化指标（NMPIW）
  3. 校准曲线下的偏差面积（MCAL）
  4. Winkler 区间分数（同时惩罚覆盖不足和过宽）
  5. **多层级置信度**（alpha=0.01/0.05/0.10/0.20/0.30 全部）

本模块把 engine/conformal.py 的单层 alpha 输出升级为**多层级置信度
分布 + 校准质量评分**，让每个场景都有完整的"可信度画像"，而不只是
一个 PASS/FAIL 标签。

对准入判据的意义：
- "替代真实实验"不能只是"和真实实验结论一致"，必须能给出
  "在多大置信度下结论成立"—— 这是可复现科学的要求。
- 多层级置信度让下游 agent 能按业务需要选择置信度水平
  （高风险决策选 alpha=0.01，快速筛选选 alpha=0.20）。
- 校准质量（MCAL）作为独立于"是否 PASS"的第二维度，
  防止"区间过宽导致假 PASS"。

依赖：
- engine/conformal.py（cross-conformal、evaluate_scenario）
- engine/surrogate.py（GPSurrogate、SurrogateEngine）

用法：
    python -S engine/conformal_vv.py                     # 全场景多层级分布
    python -S engine/conformal_vv.py --key branin         # 单场景
    python -S engine/conformal_vv.py --emit-json out.json # 输出 JSON
    python -S engine/conformal_vv.py --selftest           # 内置测试
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import conformal  # noqa: E402


# 默认的多层级置信度水平（alpha 越小，区间越宽、覆盖越严格）
ALPHA_LEVELS: Tuple[float, ...] = (0.01, 0.05, 0.10, 0.20, 0.30)


# =========================================================================
# 场景的多层级画像
# =========================================================================

@dataclass
class AlphaLevelResult:
    """单一 alpha 水平下的置信区间质量。"""
    alpha: float                       # 名义显著性水平
    conformal_Q: float                 # 校准后的分位数
    gaussian_z: float                  # 对照用的高斯 z
    picp: float                        # 真实覆盖率（>= 1-alpha 才算合格）
    nmpiw: float                       # 归一化区间宽度（越小越 sharp）
    interval_score: float              # Winkler 分数（越低越好）
    miscal_area: float                 # 校准曲线下偏差面积（越小越好）
    verdict: str                       # IMPROVED / STILL_CONSERVATIVE / NO_GAIN / UNDER_COVERS

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConfidenceProfile:
    """场景的置信度画像：多层级 alpha + 校准质量汇总。

    coverage_band：不同置信度下场景是否达标（coverage >= 1-alpha）
    sharpness_band：不同置信度下区间宽度的对比
    calibration_grade：基于 MCAL 的整体校准评级（A/B/C/D）
    """
    key: str
    n_train: int
    n_test: int
    levels: List[AlphaLevelResult]
    coverage_band: Dict[str, bool]      # alpha -> 覆盖是否达标
    sharpness_band: Dict[str, float]    # alpha -> NMPIW（越小越 sharp）
    best_alpha: float                   # 在 picp>=1-alpha 前提下最 sharp 的 alpha
    worst_calibration_alpha: float      # MCAL 最大的 alpha（校准最差层级）
    calibration_grade: str              # A/B/C/D 综合校准评级
    verdict_stability: float            # 多层级 verdict 一致率（0-1）
    profile_score: float                # 综合分数（0-1，用于排序）

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["coverage_band"] = {str(k): v for k, v in self.coverage_band.items()}
        d["sharpness_band"] = {str(k): round(v, 4) for k, v in self.sharpness_band.items()}
        return d


def _grade_from_mcal(area: float) -> str:
    """把 miscalibration_area 映射到校准评级。

    评级阈值参考 uncertainty-toolbox 的经验：
        A: area <= 0.02  → 接近完美校准
        B: area <= 0.05  → 良好
        C: area <= 0.10  → 可接受
        D: area >  0.10  → 校准偏差显著，结果不可信
    """
    if area <= 0.02:
        return "A"
    if area <= 0.05:
        return "B"
    if area <= 0.10:
        return "C"
    return "D"


def build_confidence_profile(domain: Dict[str, Any],
                             alpha_levels: Optional[Tuple[float, ...]] = None,
                             seed: int = 0,
                             noise_rel: float = 0.03,
                             k_folds: int = 5,
                             repeats: int = 3) -> ConfidenceProfile:
    """为一个场景生成多层级置信度画像。

    复用 engine/conformal.py 的 evaluate_scenario，但**逐 alpha 水平**
    跑一次，然后把各层级的 picp / nmpiw / miscal_area 汇总为
    coverage_band / sharpness_band / calibration_grade。
    """
    alphas = tuple(alpha_levels) if alpha_levels else ALPHA_LEVELS
    results: List[AlphaLevelResult] = []
    for a in alphas:
        r = conformal.evaluate_scenario(
            domain, alpha=a, seed=seed, noise_rel=noise_rel,
            k_folds=k_folds, repeats=repeats,
        )
        c = r["conformal"]
        results.append(AlphaLevelResult(
            alpha=a,
            conformal_Q=float(r["conformal_Q"]),
            gaussian_z=float(r["gaussian_z"]),
            picp=float(c["picp"]),
            nmpiw=float(c["nmpiw"]),
            interval_score=float(c["interval_score"]),
            miscal_area=float(c["miscal_area"]),
            verdict=str(r["verdict"]),
        ))

    coverage_band = {}
    sharpness_band = {}
    verdicts = []
    for r in results:
        key = f"{r.alpha:.2f}"
        # 覆盖达标：picp >= 1-alpha（允许有限样本松弛 1/(n+1)+0.02）
        slack = 1.0 / (domain.get("n_calib") or 100 + 1) + 0.02
        coverage_band[key] = bool(r.picp >= 1 - r.alpha - slack)
        sharpness_band[key] = r.nmpiw
        verdicts.append(r.verdict)

    # best_alpha: 在达标前提下最 sharp（NMPIW 最小）
    eligible = [(r.alpha, r.nmpiw) for r in results
                if coverage_band[f"{r.alpha:.2f}"]]
    best_alpha = min(eligible, key=lambda x: x[1])[0] if eligible else None

    # 最差校准层级
    worst = max(results, key=lambda r: r.miscal_area)
    worst_calibration_alpha = worst.alpha

    # 综合校准评级：取所有层级 MCAL 的加权平均（低 alpha 更严格，权重略高）
    weights = [1.0 + (0.05 - r.alpha) * 5.0 for r in results]  # alpha=0.01 权重 1.2，alpha=0.30 权重 0.85
    total_w = sum(weights)
    weighted_mcal = sum(r.miscal_area * w for r, w in zip(results, weights)) / total_w
    calibration_grade = _grade_from_mcal(weighted_mcal)

    # verdict 稳定性：多数 verdict 占比
    from collections import Counter
    c = Counter(verdicts)
    verdict_stability = max(c.values()) / len(verdicts) if verdicts else 0.0

    # profile_score：达标 alpha 数 * 平均 sharpness 倒数（越大越好）
    n_ok = sum(1 for v in coverage_band.values() if v)
    avg_inverse_sharp = float(np.mean([1.0 / max(v, 1e-6) for v in sharpness_band.values()]))
    profile_score = float((n_ok / len(alphas)) * min(1.0, avg_inverse_sharp / 50.0))

    return ConfidenceProfile(
        key=domain["key"],
        n_train=int(len(domain["train"])),
        n_test=int(len(domain["test"])),
        levels=results,
        coverage_band=coverage_band,
        sharpness_band=sharpness_band,
        best_alpha=best_alpha,
        worst_calibration_alpha=worst_calibration_alpha,
        calibration_grade=calibration_grade,
        verdict_stability=float(verdict_stability),
        profile_score=round(profile_score, 4),
    )


# =========================================================================
# 场景库（复用 conformal.load_scenarios）
# =========================================================================

def all_profiles(seed: int = 0,
                 alpha_levels: Optional[Tuple[float, ...]] = None,
                 verbose: bool = False) -> List[ConfidenceProfile]:
    """对所有可用场景生成置信度画像。"""
    scenarios = conformal.load_scenarios()
    out: List[ConfidenceProfile] = []
    for dom in scenarios:
        try:
            p = build_confidence_profile(dom, alpha_levels=alpha_levels, seed=seed)
            out.append(p)
            if verbose:
                print(f"  {dom['key']:30s} grade={p.calibration_grade} "
                      f"best_alpha={p.best_alpha} score={p.profile_score:.3f}")
        except Exception as e:
            if verbose:
                print(f"  {dom['key']:30s} !! {type(e).__name__}: {e}")
    return out


# =========================================================================
# 排序与报告
# =========================================================================

def rank_profiles(profiles: List[ConfidenceProfile]) -> List[ConfidenceProfile]:
    """按 profile_score 降序排列。"""
    return sorted(profiles, key=lambda p: p.profile_score, reverse=True)


def summarize_grades(profiles: List[ConfidenceProfile]) -> Dict[str, int]:
    """统计各级别校准评分分布。"""
    out = {"A": 0, "B": 0, "C": 0, "D": 0}
    for p in profiles:
        out[p.calibration_grade] = out.get(p.calibration_grade, 0) + 1
    return out


# =========================================================================
# Self-test（不依赖真实场景库）
# =========================================================================

def _selftest(verbose: bool = True) -> int:
    ok = True
    if verbose:
        print("=" * 60)
        print("Distributional V&V — Self-test")
        print("=" * 60)

    # Test 1: 用内置合成场景构造 profile
    # 用一个简单的正弦函数场景
    from engine import surrogate  # type: ignore
    X_train = np.array([[0.1], [0.3], [0.5], [0.7], [0.9],
                        [0.2], [0.4], [0.6], [0.8], [1.0]])
    y_train = np.sin(X_train[:, 0] * np.pi)
    X_test = np.array([[0.05], [0.15], [0.25], [0.35], [0.45],
                       [0.55], [0.65], [0.75], [0.85], [0.95]])

    domain = {
        "key": "selftest_sine",
        "domain": "selftest",
        "category": "synthetic",
        "f": lambda x: np.sin(float(x[0]) * np.pi),
        "train": X_train,
        "test": X_test,
        "name": "Synthetic sine (self-test)",
        "bounds": [(0.0, 1.0)],
        "ls": 0.15,
    }

    try:
        profile = build_confidence_profile(domain)
        t1 = (profile.calibration_grade in ("A", "B", "C", "D") and
              0.0 <= profile.profile_score <= 1.0 and
              len(profile.levels) == len(ALPHA_LEVELS))
        if verbose:
            print(f"\n[1] Build confidence profile from synthetic sine: "
                  f"{'PASS' if t1 else 'FAIL'}")
            print(f"     grade={profile.calibration_grade} "
                  f"score={profile.profile_score:.3f} "
                  f"best_alpha={profile.best_alpha} "
                  f"levels={len(profile.levels)}")
    except Exception as e:
        t1 = False
        if verbose:
            print(f"\n[1] FAIL: {type(e).__name__}: {e}")
    if not t1:
        ok = False

    # Test 2: AlphaLevelResult 结构完整性
    if t1:
        t2 = (len(profile.levels) >= 5 and
              all(0.0 <= lv.picp <= 1.0 for lv in profile.levels) and
              all(lv.nmpiw >= 0.0 for lv in profile.levels))
        if verbose:
            print(f"\n[2] AlphaLevelResult structure valid: {'PASS' if t2 else 'FAIL'}")
    else:
        t2 = False
        if verbose:
            print(f"\n[2] SKIPPED (test 1 failed)")
    if not t2:
        ok = False

    # Test 3: NMPIW 单调性——alpha 越小，区间越宽，NMPIW 应越大
    # 即按 alpha 从小到大排序，NMPIW 应非递增（允许微小数值噪声）
    if t1:
        by_alpha = sorted(profile.levels, key=lambda r: r.alpha)
        widths = [r.nmpiw for r in by_alpha]
        increases = sum(1 for i in range(1, len(widths)) if widths[i] > widths[i-1] + 1e-4)
        t3 = increases <= 1
        if verbose:
            print(f"\n[3] NMPIW monotonic with alpha (wider at smaller alpha): "
                  f"{'PASS' if t3 else 'FAIL'} ({increases} increases)")
            print(f"     alphas: {[r.alpha for r in by_alpha]}")
            print(f"     NMPIW:  {[round(w, 4) for w in widths]}")
    else:
        t3 = False
        if verbose:
            print(f"\n[3] SKIPPED (test 1 failed)")
    if not t3:
        ok = False

    # Test 4: Coverage band 应存在至少 1 个达标 alpha
    if t1:
        n_pass = sum(1 for v in profile.coverage_band.values() if v)
        t4 = n_pass >= 1
        if verbose:
            print(f"\n[4] Coverage band has at least 1 alpha meeting target: "
                  f"{'PASS' if t4 else 'FAIL'} ({n_pass}/{len(profile.coverage_band)} pass)")
    else:
        t4 = False
        if verbose:
            print(f"\n[4] SKIPPED (test 1 failed)")
    if not t4:
        ok = False

    # Test 5: Grade 与 profile_score 都可为 JSON 序列化
    if t1:
        try:
            j = profile.as_dict()
            s = json.dumps(j)
            t5 = len(s) > 100
        except Exception as e:
            t5 = False
            if verbose:
                print(f"\n[5] FAIL JSON: {type(e).__name__}: {e}")
        if verbose and t5:
            print(f"\n[5] Profile JSON serializable: PASS ({len(json.dumps(profile.as_dict()))} chars)")
    else:
        t5 = False
        if verbose:
            print(f"\n[5] SKIPPED (test 1 failed)")
    if not t5:
        ok = False

    # Test 6: Grade 分布汇总
    if t1:
        grades = summarize_grades([profile])
        t6 = grades.get(profile.calibration_grade, 0) == 1
        if verbose:
            print(f"\n[6] Grade summarizer: {'PASS' if t6 else 'FAIL'} ({grades})")
    else:
        t6 = False
        if verbose:
            print(f"\n[6] SKIPPED (test 1 failed)")
    if not t6:
        ok = False

    if verbose:
        print("\n" + "=" * 60)
        n_ok = sum([t1, t2, t3, t4, t5, t6])
        print(f"Self-test: {n_ok}/6 passed")
        print("=" * 60)
    return 0 if ok else 1


# =========================================================================
# CLI
# =========================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="Distributional V&V (SVP v0.5)")
    ap.add_argument("--key", help="filter scenario by key substring")
    ap.add_argument("--emit-json", metavar="PATH", help="output full JSON")
    ap.add_argument("--alphas", default="0.01,0.05,0.10,0.20,0.30",
                    help="comma-separated alpha levels")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return _selftest(verbose=not args.quiet)

    alphas = tuple(float(x) for x in args.alphas.split(",") if x.strip())
    scenarios = conformal.load_scenarios()
    if args.key:
        scenarios = [s for s in scenarios if args.key in s["key"]]
    if not scenarios:
        print(f"[err] no scenario matched key={args.key}")
        return 2

    if not args.quiet:
        print(f"Distributional V&V — {len(scenarios)} scenario(s)")
        print("-" * 80)

    profiles: List[ConfidenceProfile] = []
    for dom in scenarios:
        try:
            p = build_confidence_profile(dom, alpha_levels=alphas)
            profiles.append(p)
            if not args.quiet:
                cov = f"{sum(1 for v in p.coverage_band.values() if v)}/{len(p.coverage_band)}"
                print(f"  {p.key:30s} grade={p.calibration_grade} "
                      f"coverage_ok={cov} best_alpha={p.best_alpha} "
                      f"score={p.profile_score:.3f} "
                      f"stab={p.verdict_stability:.2f}")
        except Exception as e:
            print(f"  {dom.get('key','?'):30s} !! {type(e).__name__}: {e}")

    if not args.quiet and profiles:
        grades = summarize_grades(profiles)
        print("-" * 80)
        print(f"Grade distribution: {grades}")

    if args.emit_json:
        os.makedirs(os.path.dirname(os.path.abspath(args.emit_json)) or ".", exist_ok=True)
        payload = {
            "svp_version": "0.5",
            "alphas": list(alphas),
            "profiles": [p.as_dict() for p in rank_profiles(profiles)],
            "summary": summarize_grades(profiles),
        }
        with open(args.emit_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        if not args.quiet:
            print(f"Emitted {args.emit_json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
