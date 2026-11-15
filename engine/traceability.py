"""可追溯层 —— Kosmos 的核心差异化：每个结论链接到代码或论文。

Kosmos 的关键创新是「每个声明都有出处」——要么链接到 Jupyter notebook cell，
要么链接到原始文献的特定段落。这使得 AI 生成的科学报告完全可审计。

本模块为 SwarmLabs 提供同等追溯能力：

1. Finding: 一个可追溯的发现/结论
2. Provenance: 出处链接（代码行、论文 DOI、数据集）
3. Audit trail: 完整的决策历史

与 Kosmos 的差异：
- Kosmos 用平台级追踪；本模块用本地 JSON 文件
- Kosmos 链接到 notebook cell；本模块链接到代码文件:行号
- Kosmos 是商业平台；本模块是开源工具

诚实边界：
- 追溯是「已记录的出处」，不是「验证过的正确性」
- 代码行号可能随文件修改而偏移
- DOI 链接依赖外部服务的持久性
"""

from __future__ import annotations

import json
import os
import datetime
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
FINDINGS_PATH = os.path.join(DATA, "findings_trace.json")


class Finding:
    """一个可追溯的科学发现。"""

    def __init__(self, statement: str, domain: str, confidence: float = 0.0,
                 finding_type: str = "observation"):
        self.id = None  # 由 TraceabilityLayer 分配
        self.statement = statement
        self.domain = domain
        self.confidence = float(confidence)
        self.finding_type = finding_type  # observation | hypothesis | validated | refuted
        self.provenances = []  # list of Provenance
        self.created_at = datetime.datetime.now().isoformat(timespec="seconds")
        self.updated_at = self.created_at

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "statement": self.statement,
            "domain": self.domain,
            "confidence": self.confidence,
            "finding_type": self.finding_type,
            "provenances": [p.to_dict() for p in self.provenances],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Finding":
        f = cls(d["statement"], d["domain"], d.get("confidence", 0),
                d.get("finding_type", "observation"))
        f.id = d.get("id")
        f.created_at = d.get("created_at", "")
        f.updated_at = d.get("updated_at", "")
        f.provenances = [Provenance.from_dict(p) for p in d.get("provenances", [])]
        return f


class Provenance:
    """一个出处链接。"""

    def __init__(self, source_type: str, reference: str, detail: str = ""):
        self.source_type = source_type  # code | paper | dataset | observation | model
        self.reference = reference      # 文件路径、DOI、URL
        self.detail = detail            # 行号、段落、版本

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "reference": self.reference,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Provenance":
        return cls(d["source_type"], d["reference"], d.get("detail", ""))


class TraceabilityLayer:
    """可追溯层：管理发现及其出处链接。

    核心能力：
    1. 记录发现并链接到代码/论文/数据
    2. 按领域/类型/置信度查询发现
    3. 生成可审计的报告
    """

    def __init__(self, path: str = FINDINGS_PATH):
        self.path = path
        self._findings: list[Finding] = []
        self._next_id = 1
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._findings = [Finding.from_dict(fd) for fd in data.get("findings", [])]
                self._next_id = data.get("next_id", len(self._findings) + 1)
            except Exception:
                self._findings = []
                self._next_id = 1

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        data = {
            "version": 1,
            "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "next_id": self._next_id,
            "findings": [f.to_dict() for f in self._findings],
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def record_finding(self, finding: Finding) -> Finding:
        """记录一个发现，分配唯一 ID。"""
        finding.id = f"F-{self._next_id:04d}"
        self._next_id += 1
        self._findings.append(finding)
        return finding

    def add_code_provenance(self, finding_id: str, file_path: str,
                            line_start: int, line_end: int = None,
                            description: str = ""):
        """为发现添加代码出处。"""
        detail = f"L{line_start}"
        if line_end:
            detail += f"-L{line_end}"
        if description:
            detail += f" ({description})"
        for f in self._findings:
            if f.id == finding_id:
                f.provenances.append(Provenance("code", file_path, detail))
                f.updated_at = datetime.datetime.now().isoformat(timespec="seconds")
                return
        raise ValueError(f"Finding {finding_id} not found")

    def add_paper_provenance(self, finding_id: str, doi: str = "",
                             arxiv_id: str = "", title: str = "",
                             section: str = ""):
        """为发现添加论文出处。"""
        ref = doi or arxiv_id or title
        detail = f"section: {section}" if section else ""
        for f in self._findings:
            if f.id == finding_id:
                f.provenances.append(Provenance("paper", ref, detail))
                f.updated_at = datetime.datetime.now().isoformat(timespec="seconds")
                return
        raise ValueError(f"Finding {finding_id} not found")

    def add_dataset_provenance(self, finding_id: str, dataset_name: str,
                               source_url: str = "", version: str = ""):
        """为发现添加数据集出处。"""
        detail = f"version: {version}" if version else ""
        for f in self._findings:
            if f.id == finding_id:
                f.provenances.append(Provenance("dataset", dataset_name,
                                                f"{source_url} {detail}".strip()))
                f.updated_at = datetime.datetime.now().isoformat(timespec="seconds")
                return
        raise ValueError(f"Finding {finding_id} not found")

    def query_findings(self, domain: str = None, finding_type: str = None,
                       min_confidence: float = None) -> list:
        results = self._findings
        if domain:
            results = [f for f in results if f.domain == domain]
        if finding_type:
            results = [f for f in results if f.finding_type == finding_type]
        if min_confidence is not None:
            results = [f for f in results if f.confidence >= min_confidence]
        return results

    def audit_report(self) -> dict:
        """生成审计报告：每个发现的出处完整性。"""
        total = len(self._findings)
        with_provenance = sum(1 for f in self._findings if f.provenances)
        by_type = {}
        for f in self._findings:
            by_type[f.finding_type] = by_type.get(f.finding_type, 0) + 1
        by_domain = {}
        for f in self._findings:
            by_domain[f.domain] = by_domain.get(f.domain, 0) + 1

        return {
            "total_findings": total,
            "with_provenance": with_provenance,
            "coverage": with_provenance / total if total else 0,
            "by_type": by_type,
            "by_domain": by_domain,
            "unlinked": [
                {"id": f.id, "statement": f.statement[:80]}
                for f in self._findings if not f.provenances
            ],
        }

    def export_markdown(self) -> str:
        """导出为可读的 Markdown 报告。"""
        lines = ["# SwarmLabs Findings Traceability Report\n"]
        lines.append(f"Generated: {datetime.datetime.now().isoformat()}\n")
        lines.append(f"Total findings: {len(self._findings)}\n")

        for f in self._findings:
            lines.append(f"\n## [{f.id}] {f.statement[:100]}")
            lines.append(f"- Domain: {f.domain}")
            lines.append(f"- Type: {f.finding_type}")
            lines.append(f"- Confidence: {f.confidence:.2f}")
            if f.provenances:
                lines.append("- Provenance:")
                for p in f.provenances:
                    lines.append(f"  - [{p.source_type}] {p.reference} {p.detail}")
            else:
                lines.append("- **NO PROVENANCE**")

        return "\n".join(lines)
