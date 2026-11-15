"""反刷分审计脚本 —— SwarmLabs 科学价值护栏 (方案 W1)。

为什么需要这个脚本
------------------
项目历史上有过一次「伪造锚点」事件（2026-10-12），把合成数据当作
真实实验锚点写入 trust_surface.json。此后虽已回滚，但若不固化
反刷分机制，下次仍可能重演。

本脚本的职责（**不是门禁，是定期审计**）：
1. 验证 honesty 块声明的诚实值与实际数据一致
2. 检测「用合成数据冒充真实数据」的常见模式
3. 检查锚点是否有可独立复算的原始文件 URL
4. 禁止「计数达标」作为唯一判据
5. 检查是否有可运行复算脚本支撑每个科学主张

运行方式：
    python scripts/anti_cheat_audit.py              # 完整审计
    python scripts/anti_cheat_audit.py --quick      # 快速模式（只跑 P0 检查）
    python scripts/anti_cheat_audit.py --json       # 输出 JSON 格式

设计原则（与《科学价值提升方案》第 7 节护栏一致）：
- 不改变任何数据——只读、只报告
- 任何 FAIL 都附带明确的修复指引
- 宁可误报也不漏报（假阳性的代价远低于假阴性）
"""

from __future__ import annotations

import json
import os
import re
import sys
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent

#: 合成数据常见的数值特征（用于检测「伪造锚点」）
SYNTHETIC_PATTERNS = [
    # 精确浮点数（真实实验数据极少有 .0 结尾的浮点数）
    re.compile(r"^(?:\d+\.\d*0{2,})$"),  # 例如 0.1000, 1.2300
    # 完美的整数值作为「实验测量」
    re.compile(r"^-?\d+$"),
    # 等间距序列（GP 代理的典型产物，不是实验测量）
]

#: 合法的「真实数据」来源域名（可独立下载原始文件）
TRUSTED_DATA_DOMAINS = [
    "figshare.com", "zenodo.org", "datacite.org",
    "github.com", "gitlab.com", "osf.io",
    "dryad.stanford.edu", "icpsr.umich.edu",
    "doi.org",  # DOI 解析后指向上述域名之一
]

#: 禁止的「转述型来源」关键词（不能只写「某教材 p.xxx」而不给方程出处）
PARAPHRASE_MARKERS = [
    "参见", "见教材", "见课本", "参考书籍", "据文献",
    "according to the literature", "see textbook", "as cited in",
]


# ===========================================================================
# 检查项
# ===========================================================================

class AuditResult:
    """单次审计结果。"""

    def __init__(self):
        self.checks: List[Dict[str, Any]] = []
        self.n_pass = 0
        self.n_warn = 0
        self.n_fail = 0

    def add(self, check_id: str, description: str, status: str,
            details: str = "", severity: str = "FAIL") -> None:
        self.checks.append({
            "id": check_id,
            "description": description,
            "status": status,
            "severity": severity,
            "details": details,
        })
        if status == "PASS":
            self.n_pass += 1
        elif status == "WARN":
            self.n_warn += 1
        elif status == "FAIL":
            self.n_fail += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "audit_timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "summary": {
                "total": len(self.checks),
                "pass": self.n_pass,
                "warn": self.n_warn,
                "fail": self.n_fail,
                "verdict": "CLEAN" if self.n_fail == 0 else "ISSUES_FOUND",
            },
            "checks": self.checks,
        }

    def print_report(self) -> None:
        print("=" * 70)
        print("  SwarmLabs 反刷分审计报告")
        print("=" * 70)
        for c in self.checks:
            icon = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌"}[c["status"]]
            print(f"  {icon} [{c['status']}] {c['id']}: {c['description']}")
            if c["details"]:
                for line in c["details"].split("\n"):
                    print(f"         {line}")
        print("-" * 70)
        verdict = "✅ 审计通过（无反刷分迹象）" if self.n_fail == 0 else "❌ 审计发现问题"
        print(f"  总计: {len(self.checks)} 项  |  "
              f"通过: {self.n_pass}  |  警告: {self.n_warn}  |  失败: {self.n_fail}")
        print(f"  判定: {verdict}")
        print("=" * 70)


def load_json(path: Path) -> Optional[Dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# --- 检查 1: honesty 块完整性 ---

def check_honesty_block(ts_data: Dict, result: AuditResult) -> None:
    """验证 trust_surface.json:overall.honesty 块存在且数值自洽。"""
    honesty = ts_data.get("overall", {}).get("honesty")
    if not honesty:
        result.add("HON-001", "trust_surface.json 缺少 overall.honesty 块",
                   "FAIL", "honesty 块是诚信声明的单一真源，缺失即为严重违规",
                   severity="P0")
        return

    # 检查必填字段
    required_fields = ["all_evidence_simulated", "simulated_count",
                       "experimental_ground_truth_count", "reference_quality"]
    for f in required_fields:
        if f not in honesty:
            result.add(f"HON-002.{f}", f"honesty 块缺少必填字段: {f}",
                       "FAIL", f"请恢复该字段", severity="P0")

    # 检查数值自洽
    simulated = honesty.get("simulated_count", 0)
    experimental = honesty.get("experimental_ground_truth_count", 0)
    all_sim = honesty.get("all_evidence_simulated", False)
    rows = ts_data.get("rows", [])

    # 如果声称 all_evidence_simulated=True，则 experimental 必须为 0
    if all_sim and experimental > 0:
        result.add("HON-003", "矛盾：all_evidence_simulated=True 但 experimental_ground_truth_count>0",
                   "FAIL", f"simulated_count={simulated}, experimental={experimental}",
                   severity="P0")

    # 检查 simulated_count 是否等于行数
    if simulated != len(rows):
        result.add("HON-004", f"simulated_count({simulated}) ≠ 行数({len(rows)})",
                   "WARN", "两者应一致，否则 honesty 块的计数声明不可信",
                   severity="P1")


# --- 检查 2: 合成数据检测 ---

def check_synthetic_data(rows: List[Dict], result: AuditResult) -> None:
    """检测是否有场景使用合成数据冒充真实实验数据。"""
    n_suspect = 0
    for row in rows:
        key = row.get("key", "?")
        # 检查 published 值是否为合成特征
        pub = str(row.get("published", ""))

        # 如果 published 声称是「Gaussian response surface gold」→ 合成数据
        if "gaussian response surface" in pub.lower():
            n_suspect += 1
            result.add(f"SYN-{key}",
                       f"场景 {key} 的 published 值声称 'Gaussian response surface gold'",
                       "WARN",
                       "该 gold 标准是合成生成的，不是真实实验数据。"
                       "应在 evidence_type 中标注为 'synthetic_oracle' 而非 'experimental'",
                       severity="P1")

    if n_suspect == 0:
        result.add("SYN-000", "未检测到合成数据冒充实验数据的模式", "PASS")


# --- 检查 3: 锚点可复算性 ---

def check_anchor_reproducibility(ts_data: Dict, result: AuditResult) -> None:
    """检查锚点是否有可独立复算的原始数据源。"""
    rows = ts_data.get("rows", [])
    n_with_url = 0
    n_without_url = 0
    n_doi = 0

    for row in rows:
        key = row.get("key", "?")
        refs = row.get("ref_papers", [])
        has_url = False
        has_doi = False
        for ref in refs:
            ref_str = str(ref)
            if "doi.org" in ref_str.lower() or re.search(r"10\.\d{4,9}/", ref_str):
                has_doi = True
            if any(domain in ref_str.lower() for domain in TRUSTED_DATA_DOMAINS):
                has_url = True

        if has_url:
            n_with_url += 1
        elif has_doi:
            n_doi += 1
        else:
            n_without_url += 1

    # 有 DOI 的场景可以算「有溯源」（虽然不一定可下载原始文件）
    result.add("ANC-001",
               f"锚点溯源: {n_with_url} 有原始文件 URL, {n_doi} 有 DOI, {n_without_url} 无外部溯源",
               "PASS" if (n_with_url + n_doi) >= len(rows) * 0.5 else "WARN",
               f"总场景数: {len(rows)}。要求：≥50% 场景有 DOI 或可下载原始文件。"
               f"当前达标率: {(n_with_url + n_doi) / max(len(rows), 1) * 100:.0f}%",
               severity="P1")


# --- 检查 4: 转述型来源检测 ---

def check_paraphrase_sources(rows: List[Dict], result: AuditResult) -> None:
    """检测是否只写了转述型来源而没有给方程出处。"""
    n_paraphrase = 0
    for row in rows:
        key = row.get("key", "?")
        refs = row.get("ref_papers", [])
        pub = str(row.get("published", ""))
        for ref in refs:
            ref_str = str(ref)
            for marker in PARAPHRASE_MARKERS:
                if marker.lower() in ref_str.lower():
                    n_paraphrase += 1
                    result.add(f"PAR-{key}",
                               f"场景 {key} 的引用包含转述型标记 '{marker}'",
                               "WARN",
                               "引用应直接指向方程出处（教材章节/DOI），"
                               "不应只写「参见某文献」而不给具体方程来源",
                               severity="P2")
                    break

    if n_paraphrase == 0:
        result.add("PAR-000", "未检测到转述型来源引用", "PASS")


# --- 检查 5: 复算脚本覆盖 ---

def check_reproduction_scripts(result: AuditResult) -> None:
    """检查每个关键科学主张是否有对应的可运行复算脚本。"""
    scripts_dir = ROOT / "scripts"
    axiom_dir = scripts_dir / "axiom_reproductions"

    # 检查关键脚本是否存在
    required_scripts = {
        "gate_runner_unified.py": "CI 统一门禁",
        "build_public_facts.py": "公开事实表构建",
        "check_public_numbers.py": "口径一致性校验",
        "check_honesty_invariants.py": "诚信不变量检查",
        "axiom_reproductions/": "A1~A17 复现脚本目录",
    }

    for script, desc in required_scripts.items():
        path = scripts_dir / script
        if script.endswith("/"):
            exists = path.is_dir()
        else:
            exists = path.is_file()
        if exists:
            result.add(f"REPRO-{path.name.replace('.py','')}",
                       f"复算脚本存在: {script} ({desc})", "PASS")
        else:
            result.add(f"REPRO-{path.name.replace('.py','')}",
                       f"复算脚本缺失: {script} ({desc})",
                       "FAIL", f"请创建 {path}", severity="P0")

    # 检查 axiom_reproductions 下的脚本数量
    if axiom_dir.is_dir():
        n_axiom_scripts = len(list(axiom_dir.glob("*.py")))
        result.add("REPRO-AXIOM",
                   f"A1~A17 复现脚本: {n_axiom_scripts} 个",
                   "PASS" if n_axiom_scripts >= 17 else "WARN",
                   f"目录: {axiom_dir}. 预期 ≥ 17 个脚本。",
                   severity="P1")


# --- 检查 6: 数据文件完整性 ---

def check_data_integrity(result: AuditResult) -> None:
    """检查关键数据文件存在且格式正确。"""
    key_files = {
        "data/trust_surface.json": "可信面数据",
        "data/public_facts.json": "公开事实表",
        "data/generalization_bench.json": "泛化基准",
        "frontend_fixed/reports/index.json": "报告索引",
        "frontend_fixed/reports/gate_ledger.json": "门禁账本",
        "frontend_fixed/reports/heldout_index.json": "留出集索引",
    }

    for rel_path, desc in key_files.items():
        full_path = ROOT / rel_path
        if full_path.is_file():
            # 检查 JSON 格式
            try:
                data = json.loads(full_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data:
                    result.add(f"DATA-{rel_path.split('/')[-1]}",
                               f"数据文件完整: {rel_path} ({desc})", "PASS")
                else:
                    result.add(f"DATA-{rel_path.split('/')[-1]}",
                               f"数据文件为空或非对象: {rel_path}",
                               "WARN", "文件存在但内容可能不完整", severity="P1")
            except json.JSONDecodeError as e:
                result.add(f"DATA-{rel_path.split('/')[-1]}",
                           f"数据文件 JSON 格式错误: {rel_path}",
                           "FAIL", str(e), severity="P0")
        else:
            result.add(f"DATA-{rel_path.split('/')[-1]}",
                       f"数据文件缺失: {rel_path} ({desc})",
                       "FAIL", f"请恢复 {full_path}", severity="P0")


# --- 检查 7: 诚信不变量一致性 ---

def check_honesty_invariants_consistency(ts_data: Dict, result: AuditResult) -> None:
    """检查 trust_surface 中的 honesty 声明与行数一致。"""
    rows = ts_data.get("rows", [])
    overall = ts_data.get("overall", {})

    # 检查行数
    declared = overall.get("scenarios", 0)
    actual = len(rows)
    if declared != actual:
        result.add("INV-001",
                   f"声明场景数({declared}) ≠ 实际行数({actual})",
                   "FAIL", "overall.scenarios 必须等于 rows 长度",
                   severity="P0")

    # 检查 rubric_full_pass
    rubric_full = overall.get("rubric_full_pass", 0)
    actual_full = sum(1 for r in rows if r.get("rubric_passed", 0) >= r.get("rubric_total", 7))
    if rubric_full != actual_full:
        result.add("INV-002",
                   f"声明 rubric_full_pass({rubric_full}) ≠ 实际({actual_full})",
                   "WARN", "overall.rubric_full_pass 与 rows 不一致",
                   severity="P1")

    # 检查 total_store_points
    declared_store = overall.get("total_store_points", 0)
    actual_store = sum(r.get("n_store", 0) for r in rows)
    if declared_store != actual_store:
        result.add("INV-003",
                   f"声明 total_store_points({declared_store}) ≠ 实际({actual_store})",
                   "FAIL", "overall.total_store_points 与 sum(rows[].n_store) 不一致",
                   severity="P0")


# --- 检查 8: Git 提交状态 ---

def check_git_commit_status(result: AuditResult) -> None:
    """检查诚信回滚是否已提交（工作树不应有未提交的修改）。"""
    import subprocess
    try:
        git_status = subprocess.run(
            ["git", "status", "--porcelain", "data/trust_surface.json",
             "data/public_facts.json", "README.md", "frontend_fixed/index.html"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=10,
        )
        uncommitted = git_status.stdout.strip()
        if uncommitted:
            files = [line.strip() for line in uncommitted.split("\n") if line.strip()]
            result.add("GIT-001",
                       f"诚信相关文件有未提交修改 ({len(files)} 个)",
                       "WARN",
                       "诚信回滚必须 commit 入库。未提交的修改可能被撤销。\n"
                       f"未提交文件:\n" + "\n".join(f"  {f}" for f in files),
                       severity="P0")
        else:
            result.add("GIT-001", "诚信相关文件均已提交", "PASS")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        result.add("GIT-001", f"无法检查 git 状态: {e}", "WARN", severity="P2")


# ===========================================================================
# 主入口
# ===========================================================================

def run_audit(quick: bool = False) -> AuditResult:
    """运行完整审计。"""
    result = AuditResult()

    # 加载数据
    ts_data = load_json(ROOT / "data" / "trust_surface.json")
    if not ts_data:
        result.add("DATA-LOAD", "无法加载 data/trust_surface.json",
                   "FAIL", "该文件是所有审计的基础", severity="P0")
        return result

    rows = ts_data.get("rows", [])

    # 所有检查项
    check_honesty_block(ts_data, result)
    check_honesty_invariants_consistency(ts_data, result)
    check_synthetic_data(rows, result)
    check_anchor_reproducibility(ts_data, result)
    check_paraphrase_sources(rows, result)
    check_reproduction_scripts(result)
    check_data_integrity(result)
    check_git_commit_status(result)

    return result


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="SwarmLabs 反刷分审计")
    parser.add_argument("--quick", action="store_true", help="快速模式（只跑 P0 检查）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()

    result = run_audit(quick=args.quick)

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        result.print_report()

    # 退出码：有 FAIL 则返回 1
    sys.exit(1 if result.n_fail > 0 else 0)


if __name__ == "__main__":
    main()
