"""A1~A17 失效模式复现库 - 统一运行入口。

用法：
    python scripts/axiom_repro/run_all.py          # 运行全部
    python scripts/axiom_repro/run_all.py --list   # 列出所有脚本
    python scripts/axiom_repro/run_all.py a15      # 运行单个

每个脚本独立运行，返回 0 (成功复现) 或 非0 (失败)。
"""
from __future__ import annotations

import os
import sys
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
PYTHON = sys.executable

# 17 个复现脚本（按 AXIOM_AUDIT.md 编号）
SCRIPTS = [
    ("a1",  "a1_scenario_representativeness.py",  "A1 场景代表性假设"),
    ("a2",  "a2_domain_skewness.py",               "A2 领域集中度假设"),
    ("a3",  "a3_conformal_exchangeability.py",     "A3 Conformal 可交换性"),
    ("a4",  "a4_kernel_choice.py",                 "A4 GP 核函数假设"),
    ("a5",  "a5_numerical_precision.py",           "A5 数值精度假设"),
    ("a6",  "a6_threshold_sensitivity.py",         "A6 阈值选择假设"),
    ("a7",  "a7_dependency_integrity.py",          "A7 外部依赖正确性"),
    ("a8",  "a8_wet_lab_anchor.py",                "A8 湿实验锚定声明"),
    ("a9",  "a9_scenario_independence.py",         "A9 场景独立假设"),
    ("a10", "a10_single_point_eval.py",            "A10 单点评估假设"),
    ("a11", "a11_adversarial_robustness.py",       "A11 对抗性鲁棒性"),
    ("a12", "a12_lean_trust_chain.py",             "A12 Lean 信任链"),
    ("a13", "a13_family_consistency.py",           "A13 机理先验同族性"),
    ("a14", "a14_applicability_domain.py",         "A14 适用域边界"),
    ("a15", "a15_extrapolation_gap.py",            "A15 GP 外推退化"),
    ("a16", "a16_semantic_alignment.py",           "A16 语义对齐不能救 G3"),
    ("a17", "a17_log_scale.py",                    "A17 对数表示"),
]


def list_scripts():
    """列出所有复现脚本。"""
    print("A1~A17 失效模式复现脚本:")
    print("=" * 65)
    print(f"{'Key':<5} {'脚本':<42} {'描述':<25}")
    print("-" * 65)
    for key, script, desc in SCRIPTS:
        exists = os.path.exists(os.path.join(SCRIPT_DIR, script))
        mark = "✅" if exists else "❌"
        print(f"{key:<5} {mark} {script:<40} {desc:<25}")
    print(f"\n总计: {len(SCRIPTS)} 个脚本")


def run_script(key: str, timeout: int = 120) -> tuple[str, bool]:
    """运行单个复现脚本。"""
    if key not in dict(SCRIPTS):
        return (f"未知脚本: {key}", False)

    script_name = dict(SCRIPTS)[key][1]
    desc = dict(SCRIPTS)[key][2]
    script_path = os.path.join(SCRIPT_DIR, script_name)

    if not os.path.exists(script_path):
        return (f"{key}: 脚本不存在 ({script_name})", False)

    print(f"\n{'=' * 60}")
    print(f"运行 {key}: {desc}")
    print(f"脚本: {script_name}")
    print("=" * 60)

    try:
        result = subprocess.run(
            [PYTHON, script_path],
            capture_output=True, text=True, timeout=timeout, cwd=ROOT
        )
        # 显示输出
        if result.stdout:
            # 只显示最后 500 字符
            output = result.stdout.strip()
            if len(output) > 500:
                output = "... " + output[-500:]
            print(output)

        if result.returncode == 0:
            return (f"{key}: ✅ 成功复现", True)
        else:
            err = result.stderr.strip()[-200:] if result.stderr else ""
            return (f"{key}: ❌ 失败 (exit={result.returncode}) {err}", False)

    except subprocess.TimeoutExpired:
        return (f"{key}: ⏱️ 超时 ({timeout}s)", False)
    except Exception as e:
        return (f"{key}: 异常 - {e}", False)


def run_all(keys: list[str] = None):
    """运行所有或指定的复现脚本。"""
    if keys is None:
        keys = [k for k, _, _ in SCRIPTS]

    results = {}
    for key in keys:
        status, success = run_script(key)
        results[key] = (status, success)

    # 汇总
    print("\n" + "=" * 60)
    print("汇总")
    print("=" * 60)
    total = len(results)
    passed = sum(1 for _, (s, ok) in results.items() if ok)
    print(f"总计: {total}")
    print(f"通过: {passed}")
    print(f"失败: {total - passed}")
    print(f"通过率: {passed/total*100:.1f}%" if total > 0 else "无脚本运行")
    print("\n详细结果:")
    for key, (status, success) in results.items():
        print(f"  {status}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="运行 A1~A17 复现脚本")
    parser.add_argument("--list", action="store_true", help="列出所有脚本")
    parser.add_argument("keys", nargs="*", help="指定脚本 key（如 a15 a14）")
    args = parser.parse_args()

    if args.list:
        list_scripts()
        return

    if args.keys:
        run_all(args.keys)
    else:
        run_all()


if __name__ == "__main__":
    main()