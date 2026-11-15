# SwarmLabs Promote Gate: Verifier-Based Migration Plan

**Date**: 2026-09-22  
**Status**: Draft  
**Author**: WorkBuddy  

## 背景

SwarmLabs 当前使用静态规则（`anchor_intake.py` 的三层筛选）决定 synthetic anchors 是否 promote 到 literature anchors。这个方案有三个问题：

1. **自指坍塌风险**：验证服务如果自己的参照数据没被独立验证过，它的可信度就建立在自己没验证的东西上。
2. **规则僵化**：三层筛选是硬编码的（年份/DOI、量纲、人工确认），无法适应新场景。
3. **无法拦截"自信地错"**：规则只能拦截"明显错"（无年份、量纲不匹配），无法拦截"看起来对但实际错"。

## 目标架构

从「静态规则」迁移到「独立验证器」：

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Synthetic      │     │  Verifier Gate   │     │  Literature     │
│  Anchor         │────▶│  (vv-gate)       │────▶│  Anchor         │
│  (candidate)    │     │  (independent)   │     │  (promoted)     │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │  Held-out Truth │
                    │  (vendor-held)   │
                    └─────────────────┘
```

**核心区别**：
- **静态规则**：规则是硬编码的，验证者是被验证者。
- **验证器**：验证者独立于被验证者，持有 held-out ground truth。

## 迁移路径

### 阶段 1：包装现有 vv-gate（最小改动）

在 `engine/` 下新增 `promote_verifier.py`，包装现有的 `vv-gate` MCP server：

```python
# engine/promote_verifier.py
"""Verifier-based promote gate for synthetic anchors.

Wraps the existing vv-gate (skills/vv-gate/scripts/vv_gate.py) to provide
an independent adjudication layer for anchor promotion decisions.

Exit codes:
    0: PROCEED - anchor can be promoted
    2: BLOCK - anchor must not be promoted (independent verification failed)
    4: UNREACHABLE - gate unavailable (no verdict exists)
"""

import subprocess
import json
import os
from typing import Optional, Dict

VAGATE_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "engine-kit", "skills", "vv-gate", "scripts", "vv_gate.py"
)

class PromoteVerifierResult:
    PROCEED = 0
    BLOCK = 2
    UNREACHABLE = 4
    ERROR = 1

class PromoteVerifier:
    def __init__(self, scenario: str, prediction_path: str):
        self.scenario = scenario
        self.prediction_path = prediction_path

    def check(self) -> Dict:
        """Run the verifier gate check.

        Returns:
            Dict with 'exit_code', 'gate', 'reason', 'details'
        """
        cmd = [
            "python", VAGATE_SCRIPT,
            "verify", self.scenario,
            "--pred", self.prediction_path,
            "--json"
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return {
                "exit_code": result.returncode,
                "gate": self._interpret_exit_code(result.returncode),
                "reason": result.stderr.strip() if result.stderr else "",
                "details": json.loads(result.stdout) if result.stdout else {},
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": PromoteVerifierResult.UNREACHABLE,
                "gate": "UNREACHABLE",
                "reason": "Verifier gate timed out",
                "details": {},
            }
        except Exception as e:
            return {
                "exit_code": PromoteVerifierResult.ERROR,
                "gate": "ERROR",
                "reason": str(e),
                "details": {},
            }

    @staticmethod
    def _interpret_exit_code(code: int) -> str:
        mapping = {
            0: "PROCEED",
            2: "BLOCK_AUTONOMOUS_ACTION",
            3: "PROCEED_WITH_HUMAN_CHECK",
            4: "UNREACHABLE",
            1: "ERROR",
        }
        return mapping.get(code, "UNKNOWN")
```

### 阶段 2：集成到 anchor_intake

修改 `anchor_intake.py`，在三层筛选后增加验证器检查：

```python
# 在 anchor_intake.py 的 screen() 函数末尾增加：

def screen_with_verifier(candidate: Dict, scenario: str) -> str:
    """Screen candidate with verifier gate.

    Args:
        candidate: Anchor candidate dict
        scenario: Scenario identifier for verifier gate

    Returns:
        ACCEPTED, NEEDS_HUMAN, or REJECTED
    """
    # 先跑现有的三层筛选
    result = screen(candidate)

    # 如果三层筛选通过，再跑验证器
    if result == ACCEPTED:
        # 准备预测文件
        pred_path = os.path.join(S.ROOT, "data", "pending_predictions.json")
        with open(pred_path, "w") as f:
            json.dump([candidate["prediction"]], f)

        # 跑验证器
        gate = PromoteVerifier(scenario, pred_path)
        verdict = gate.check()

        if verdict["gate"] == "PROCEED":
            return ACCEPTED
        elif verdict["gate"] == "BLOCK_AUTONOMOUS_ACTION":
            return REJECTED
        elif verdict["gate"] == "PROCEED_WITH_HUMAN_CHECK":
            return NEEDS_HUMAN
        else:
            # UNREACHABLE 或 ERROR，降级为 NEEDS_HUMAN
            return NEEDS_HUMAN

    return result
```

### 阶段 3：灰度切换

1. **Week 1-2**：并行运行，记录差异（现有规则 vs 验证器）
2. **Week 3-4**：验证器作为主要判据，现有规则作为备用
3. **Week 5+**：验证器作为唯一判据，现有规则下线

## 关键设计决策

### 1. 为什么用 vv-gate 而不是新建验证器？

- **已存在**：`engine-kit/skills/vv-gate/` 已经实现了完整的验证器
- **独立**：vv-gate 持有 vendor-held ground truth，与被验证者完全独立
- **零依赖**：vv-gate 是零依赖 Python 脚本，易于集成

### 2. 为什么保留现有三层筛选？

- **降级路径**：验证器不可达时，三层筛选作为备用
- **人工确认**：三层筛选的第 3 层（人工确认）是必要的，验证器不能替代
- **过渡期**：灰度切换需要并行运行

### 3. 退出码语义

- `0 (PROCEED)`：验证器通过，可以 promote
- `2 (BLOCK)`：验证器拒绝，不能 promote
- `3 (PROCEED_WITH_HUMAN_CHECK)`：验证器要求人工确认
- `4 (UNREACHABLE)`：验证器不可达，降级为人工确认
- `1 (ERROR)`：本地错误，降级为人工确认

## 实施清单

- [ ] 创建 `engine/promote_verifier.py`（阶段 1）
- [ ] 修改 `engine/anchor_intake.py` 集成验证器（阶段 2）
- [ ] 添加单元测试 `tests/test_promote_verifier.py`
- [ ] 灰度切换监控脚本 `scripts/promote_verifier_monitor.py`
- [ ] 文档更新 `docs/promote_verifier_migration.md`

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 验证器不可达 | 降级为 NEEDS_HUMAN，不阻塞流程 |
| 验证器误判 | 灰度切换期间并行运行，记录差异 |
| 性能退化 | 验证器超时 30s，超时降级 |
| 依赖 vv-gate 脚本 | vv-gate 已稳定运行，零依赖 |

## 下一步

1. 实现 `engine/promote_verifier.py`（本 PR）
2. 集成到 `anchor_intake.py`（下一个 PR）
3. 灰度切换监控（后续）

---

**参考**：
- `engine-kit/skills/vv-gate/SKILL.md` - vv-gate 完整文档
- `engine/anchor_intake.py` - 现有三层筛选实现
- `docs/feasibility-virtual-lab-2026-09-21.md` §6 - 垃圾进风险分析
