#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PromoteVerifier：独立 verifier 门禁（对齐 RSIAgent / Mano-P think-act-verify）。

**命名说明**：`engine/verifier_gate.py` 已存在（351 行，包装 engine-kit 的
vv-gate 独立裁决层）。本模块是**互补**的 —— 它验证「promote 决策前的
候选质量」，通过让独立 verifier 脚本在真实环境跑一遍候选，输出通过/失败率。

设计要点：
1. **不假设 skill 是 markdown 规则文件** —— skill 可以是任何可执行工件；
   verifier 负责"跑一次真实验证"。
2. **不信任 skill 自报的结论** —— 通过解析 verifier 脚本的 stdout 与
   写出的 predictions.jsonl 做独立判定。
3. **dry_run 可先做** —— `--dry-run` 只生成 SKILL 骨架，不执行任何脚本。
4. **退出码语义**：
     0 = 通过（成功率 >= min_success_rate 且总数 >= min_candidates）
     1 = 脚本正常跑但失败率过高（有真实候选，只是没过）
     2 = 脚本缺失（skill 还没接 verifier）
     3 = 脚本执行失败或无输出

用法：
    python -m engine.promote_verifier --project myskill --target ./skills/myskill --dry-run
    python -m engine.promote_verifier --project myskill --target ./skills/myskill --check

产出：
    {target_dir}/SKILL.md                        # 骨架，首次 --dry-run 时创建
    {target_dir}/results/predictions.jsonl       # verifier 脚本写出的判定结果
    {target_dir}/.verifier_state.json            # 状态元数据
    --status-out PATH                            # 汇总状态 JSON
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

VERIFIER_STATUS_SCHEMA = "swarmlabs.verifier_status.v1"

SKILL_TEMPLATE = """# Skill: {project}

## Purpose
{description}

## Verifier Setup
This skill uses a verifier gate before promotion.

- Dry-run init: `python -m engine.promote_verifier --project {project} --target . --dry-run`
- Real check:   `python -m engine.promote_verifier --project {project} --target . --check`

## Verifier Contract
The verifier script must:
1. Run the candidate skill against a benchmark/real environment.
2. Write results to `./results/predictions.jsonl` (JSONL format).
3. Each line: `{{"candidate_id": "...", "verdict": "pass|fail", "checks": [...]}}`.

## Status
Verifier status is written to `./.verifier_state.json` after each run.
Exit code 0 = pass; 1 = fail; 2 = script missing; 3 = script failed.
"""


@dataclass
class CheckStatus:
    """一次 check 的完整状态。"""
    schema: str = VERIFIER_STATUS_SCHEMA
    project: str = ""
    target_dir: str = ""
    gate_pass: bool = False
    gate_exit: int = 2  # 0/1/2/3
    total: int = 0
    pass_count: int = 0
    fail_count: int = 0
    skipped_count: int = 0
    success_rate: float = 0.0
    min_success_rate: float = 0.9
    min_candidates: int = 3
    script_path: str = ""
    script_exists: bool = False
    predictions_path: str = ""
    predictions_exists: bool = False
    duration_sec: float = 0.0
    stderr_excerpt: str = ""
    stdout_excerpt: str = ""
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S+08:00"))

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00")


class PromoteVerifier:
    """skill 项目下的 verifier 门禁。"""

    def __init__(
        self,
        project: str,
        target_dir: Path,
        dry_run: bool = True,
        min_success_rate: float = 0.9,
        min_candidates: int = 3,
        script_path: Optional[Path] = None,
        max_runtime_sec: int = 180,
        description: str = "",
    ):
        self.project = project
        self.target_dir = Path(target_dir).resolve()
        self.dry_run = dry_run
        self.min_success_rate = min_success_rate
        self.min_candidates = min_candidates
        self.max_runtime_sec = max_runtime_sec
        self.description = description or f"Verifiable skill for project {project}"
        self.script_path = Path(script_path) if script_path else (self.target_dir / "scripts" / "verify.sh")

    # ── 初始化 ──────────────────────────────────────────────
    def init(self) -> Path:
        """创建 skill 骨架（含 verifier 说明）。

        即便 dry_run=True 也会真的创建目录和 SKILL.md —— 因为 --dry-run 的意义是
        "不执行脚本"，而不是"什么都不做"。
        """
        self.target_dir.mkdir(parents=True, exist_ok=True)
        skill_md = self.target_dir / "SKILL.md"
        if not skill_md.exists():
            skill_md.write_text(
                SKILL_TEMPLATE.format(project=self.project, description=self.description),
                encoding="utf-8",
            )
        # 记录元数据
        state = {
            "project": self.project,
            "target_dir": str(self.target_dir),
            "script_path": str(self.script_path),
            "dry_run": self.dry_run,
            "min_success_rate": self.min_success_rate,
            "min_candidates": self.min_candidates,
            "created_at": _now_iso(),
        }
        (self.target_dir / ".verifier_state.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        return skill_md

    # ── 执行检查 ────────────────────────────────────────────
    def _build_command(self) -> List[str]:
        """根据脚本扩展名决定如何调用。

        .py 文件用 sys.executable 调用（跨平台，Windows 也能跑）；
        .sh 文件在 Unix 系统下直接执行（依赖 shebang + chmod）。
        """
        if self.script_path.suffix == ".py":
            return [sys.executable, str(self.script_path)]
        # .sh 及其他：直接执行（Unix 下依赖 shebang）
        return [str(self.script_path)]

    def check(self, status_out: Optional[Path] = None) -> CheckStatus:
        """跑 verifier，返回状态。"""
        status = CheckStatus(
            project=self.project,
            target_dir=str(self.target_dir),
            min_success_rate=self.min_success_rate,
            min_candidates=self.min_candidates,
            script_path=str(self.script_path),
            script_exists=self.script_path.exists(),
        )

        # dry-run 下不执行脚本
        if self.dry_run:
            status.gate_exit = 0
            status.gate_pass = True
            self._write_state(status)
            if status_out:
                status_out.write_text(json.dumps(status.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
            return status

        # 脚本存在性检查
        if not status.script_exists:
            status.gate_exit = 2
            status.gate_pass = False
            self._write_state(status)
            if status_out:
                status_out.write_text(json.dumps(status.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
            return status

        # 执行脚本
        t0 = time.time()
        try:
            proc = subprocess.run(
                self._build_command(),
                cwd=str(self.target_dir),
                capture_output=True,
                text=True,
                timeout=self.max_runtime_sec,
                env={**os.environ, "PROMOTE_VERIFIER_PROJECT": self.project},
            )
            status.duration_sec = round(time.time() - t0, 3)
            status.stdout_excerpt = (proc.stdout or "")[:400]
            status.stderr_excerpt = (proc.stderr or "")[:400]
        except subprocess.TimeoutExpired:
            status.gate_exit = 3
            status.gate_pass = False
            status.stderr_excerpt = "TIMEOUT"
            self._write_state(status)
            if status_out:
                status_out.write_text(json.dumps(status.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
            return status
        except OSError as e:
            status.gate_exit = 3
            status.gate_pass = False
            status.stderr_excerpt = str(e)
            self._write_state(status)
            if status_out:
                status_out.write_text(json.dumps(status.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
            return status

        # 解析预测文件
        predict_path = self.target_dir / "results" / "predictions.jsonl"
        status.predictions_path = str(predict_path)
        status.predictions_exists = predict_path.exists()

        if not status.predictions_exists:
            status.gate_exit = 3
            status.gate_pass = False
            status.stderr_excerpt += " (no predictions.jsonl produced)"
            self._write_state(status)
            if status_out:
                status_out.write_text(json.dumps(status.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
            return status

        # 汇总
        verdicts = []
        with open(predict_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    verdicts.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        status.total = len(verdicts)
        status.pass_count = sum(1 for v in verdicts if v.get("verdict") == "pass")
        status.fail_count = sum(1 for v in verdicts if v.get("verdict") == "fail")
        status.skipped_count = status.total - status.pass_count - status.fail_count
        status.success_rate = round(
            status.pass_count / status.total if status.total else 0.0, 4
        )

        # 判定
        if status.total < status.min_candidates:
            status.gate_exit = 3  # 数据太少
            status.gate_pass = False
        elif status.success_rate >= status.min_success_rate:
            status.gate_exit = 0
            status.gate_pass = True
        else:
            status.gate_exit = 1
            status.gate_pass = False

        self._write_state(status)
        if status_out:
            status_out.write_text(json.dumps(status.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return status

    def _write_state(self, status: CheckStatus) -> None:
        state = {
            "project": status.project,
            "target_dir": status.target_dir,
            "last_check_at": status.created_at,
            "gate_pass": status.gate_pass,
            "gate_exit": status.gate_exit,
            "total": status.total,
            "success_rate": status.success_rate,
            "min_success_rate": status.min_success_rate,
            "min_candidates": status.min_candidates,
        }
        (self.target_dir / ".verifier_state.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8",
        )


def interpret_exit_code(code: int) -> str:
    return {0: "ok", 1: "fail", 2: "script_missing", 3: "script_failed"}.get(code, "unknown")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="PromoteVerifier：独立 verifier 门禁")
    ap.add_argument("--project", required=True, help="项目名（唯一标识）")
    ap.add_argument("--target", required=True, help="skill 目录路径")
    ap.add_argument("--dry-run", action="store_true", help="只初始化骨架，不执行脚本")
    ap.add_argument("--check", action="store_true", help="执行 verifier 检查")
    ap.add_argument("--status-out", help="状态 JSON 输出路径")
    ap.add_argument("--min-success-rate", type=float, default=0.9)
    ap.add_argument("--min-candidates", type=int, default=3)
    ap.add_argument("--script", help="verifier 脚本路径（默认 target_dir/scripts/verify.sh）")
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args(argv)

    if not args.dry_run and not args.check:
        ap.error("必须指定 --dry-run 或 --check")

    target = Path(args.target)
    verifier = PromoteVerifier(
        project=args.project,
        target_dir=target,
        dry_run=args.dry_run,
        min_success_rate=args.min_success_rate,
        min_candidates=args.min_candidates,
        script_path=Path(args.script) if args.script else None,
        max_runtime_sec=args.timeout,
    )

    if args.dry_run:
        verifier.init()
        print(json.dumps({"project": args.project, "target_dir": str(verifier.target_dir), "dry_run": True}, indent=2))
        return 0

    status_out = Path(args.status_out) if args.status_out else None
    status = verifier.check(status_out=status_out)
    print(json.dumps(status.to_dict(), indent=2, ensure_ascii=False))
    return status.gate_exit


if __name__ == "__main__":
    sys.exit(main())
